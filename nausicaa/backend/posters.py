"""Affiches de la bibliothèque, sans Internet : dans l'ordre
  1. une image choisie par l'utilisateur (clic droit « Choisir une image… ») ;
  2. une image posée à côté (convention de Kodi / Jellyfin) : « poster », « folder », « cover », « front »
     dans le dossier de la série ou d'une saison ; « Film-poster.jpg » / « Film.jpg » à côté d'un film, ou
     « poster.jpg »… dans son dossier s'il y est seul ;
  (2 bis. option « tmdb » : l'affiche de themoviedb.org, backend/tmdb.py ;)
  3. la vignette que GNOME a déjà faite (~/.cache/thumbnails, spécification freedesktop) ;
  4. une image tirée de la vidéo (20 % de la durée) par la libmpv de Nausicaa — rien de plus à installer.
Chaque affiche est recadrée au format 2:3 et rangée dans ~/.cache/nausicaa/posters (clé : source + taille +
date). Un seul fil de travail, une demande à la fois, la plus récente d'abord ; réponse dans la boucle GLib.
"""

from __future__ import annotations

import hashlib
import os
import queue
import shutil
import tempfile
import threading
from typing import Callable, Optional

from ..settings import CACHE_DIR

POSTER_DIR = os.path.join(CACHE_DIR, "posters")
WIDTH, HEIGHT = 300, 450
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
NAMES = ("poster", "folder", "cover", "front", "affiche")
FRAME_AT = "20%"
FRAME_TIMEOUT = 20.0


def _images(folder: str) -> dict:
    """{nom sans extension en minuscules: chemin} des images d'un dossier."""
    out = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        stem, ext = os.path.splitext(n)
        if ext.lower() in IMAGE_EXTS and not n.startswith("."):
            out.setdefault(stem.casefold(), os.path.join(folder, n))
    return out


def local_image(kind: str, target, alone: bool = False) -> Optional[str]:
    """Image posée à côté. kind « show » : target = liste de dossiers (série, saisons) ; « film » : chemin
    de la vidéo (alone : seule vidéo de son dossier, les noms génériques valent alors pour elle)."""
    if kind == "show":
        for folder in target:
            imgs = _images(folder)
            for n in NAMES:
                if n in imgs:
                    return imgs[n]
        return None
    folder = os.path.dirname(target)
    stem = os.path.splitext(os.path.basename(target))[0].casefold()
    imgs = _images(folder)
    for n in (stem + "-poster", stem + ".poster", stem):
        if n in imgs:
            return imgs[n]
    if alone:
        for n in NAMES:
            if n in imgs:
                return imgs[n]
    return None


def gnome_thumbnail(path: str) -> Optional[str]:
    """Vignette déjà faite par GNOME (Nautilus) pour cette vidéo, si elle est à jour."""
    try:
        from gi.repository import Gio
        uri = Gio.File.new_for_path(path).get_uri()
        mtime = os.stat(path).st_mtime
    except (OSError, ImportError):
        return None
    digest = hashlib.md5(uri.encode("utf-8")).hexdigest()
    base = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "thumbnails")
    for size in ("xx-large", "x-large", "large", "normal"):
        p = os.path.join(base, size, digest + ".png")
        try:
            if os.stat(p).st_mtime >= mtime:
                return p
        except OSError:
            continue
    return None


def grab_frame(video: str, out_dir: str) -> Optional[str]:
    """Une image de la vidéo par la libmpv (vo=image), sans fenêtre ni son. None si illisible."""
    import mpv
    m = None
    try:
        m = mpv.MPV(vo="image", vo_image_format="jpg", vo_image_outdir=out_dir, frames="1", start=FRAME_AT,
                    ao="null", aid="no", sid="no", hwdec="no", config=False, osd_level="0",
                    load_scripts=False, ytdl=False)
        m.play(video)
        try:
            m.wait_for_event("end-file", timeout=FRAME_TIMEOUT)
        except Exception:  # noqa: BLE001 — délai dépassé : rien
            pass
    except Exception:  # noqa: BLE001
        return None
    finally:
        if m is not None:
            try:
                m.terminate()
            except Exception:  # noqa: BLE001
                pass
    for n in sorted(os.listdir(out_dir)):
        if n.lower().endswith(".jpg"):
            return os.path.join(out_dir, n)
    return None


def make_poster(src: str, dest: str) -> bool:
    """Image recadrée au centre en 2:3, WIDTH × HEIGHT, JPEG ; écrite sous un nom caché puis renommée."""
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib
    try:
        pix = GdkPixbuf.Pixbuf.new_from_file(src)
        pix = pix.apply_embedded_orientation() or pix
        w, h = pix.get_width(), pix.get_height()
        if w * 3 > h * 2:                         # trop large : on garde le milieu
            cw, ch = h * 2 // 3, h
        else:
            cw, ch = w, w * 3 // 2
        cw, ch = max(1, min(cw, w)), max(1, min(ch, h))
        crop = pix.new_subpixbuf((w - cw) // 2, (h - ch) // 2, cw, ch)
        scaled = crop.scale_simple(WIDTH, HEIGHT, GdkPixbuf.InterpType.BILINEAR)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        scaled.savev(tmp, "jpeg", ["quality"], ["85"])
        os.replace(tmp, dest)
        return True
    except (GLib.Error, OSError, TypeError):
        return False


def cache_path(source: str, kind: str) -> Optional[str]:
    try:
        st = os.stat(source)
    except OSError:
        return None
    key = f"{kind}|{os.path.realpath(source)}|{st.st_size}|{int(st.st_mtime)}"
    return os.path.join(POSTER_DIR, hashlib.sha1(key.encode("utf-8", "surrogateescape")).hexdigest() + ".jpg")


def poster_for(kind: str, target, video: str, alone: bool = False, chosen: str = "",
               tmdb_query: Optional[tuple] = None) -> Optional[str]:
    """Affiche (fichier du cache) d'une série (target = dossiers) ou d'un film (target = la vidéo). video =
    la vidéo dont tirer une image s'il n'y a rien d'autre. tmdb_query = (titre, année, clé, langue) quand la
    fonctionnalité « tmdb » est active : essayée après les images de l'utilisateur, avant les images tirées
    des vidéos. Lent (disque, réseau, mpv) : dans un fil."""
    for src, how in ((chosen, "image") if chosen and os.path.isfile(chosen) else (None, ""),
                     (local_image(kind, target, alone), "image")):
        if src:
            dest = cache_path(src, how)
            if dest and (os.path.exists(dest) or make_poster(src, dest)):
                return dest
    if tmdb_query is not None:
        from . import tmdb
        title, year, key, lang = tmdb_query
        src = tmdb.poster("tv" if kind == "show" else "movie", title, year, key, lang)
        if src:
            dest = cache_path(src, "tmdb")
            if dest and (os.path.exists(dest) or make_poster(src, dest)):
                return dest
    if not video:
        return None
    dest = cache_path(video, "frame")
    if dest is None:
        return None
    if os.path.exists(dest):
        return dest
    thumb = gnome_thumbnail(video)
    if thumb and make_poster(thumb, dest):
        return dest
    tmp = tempfile.mkdtemp(prefix="nausicaa-frame-")
    try:
        frame = grab_frame(video, tmp)
        if frame and make_poster(frame, dest):
            return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return None


class PosterWorker:
    """Un fil, une affiche à la fois ; les demandes les plus récentes passent d'abord (la page affichée).
    `request(clé, fonction, rappel)` : rappel(chemin | None) dans la boucle GLib."""

    def __init__(self):
        self._jobs: list = []
        self._lock = threading.Lock()
        self._wake = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self.generation = 0

    def request(self, key: str, fn: Callable[[], Optional[str]], callback: Callable[[Optional[str]], None]):
        with self._lock:
            self._jobs = [j for j in self._jobs if j[0] != key]
            self._jobs.append((key, fn, callback, self.generation))
        self._wake.put(1)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="nausicaa-posters")
            self._thread.start()

    def cancel_all(self):
        """Page quittée ou relue : les demandes en attente ne servent plus."""
        with self._lock:
            self._jobs.clear()
            self.generation += 1

    def pending(self) -> int:
        with self._lock:
            return len(self._jobs)

    def _run(self):
        from gi.repository import GLib
        while True:
            with self._lock:
                job = self._jobs.pop() if self._jobs else None
            if job is None:
                try:
                    self._wake.get(timeout=30)
                except queue.Empty:
                    return
                continue
            key, fn, callback, gen = job
            try:
                result = fn()
            except Exception:  # noqa: BLE001 — une affiche ratée ne doit pas arrêter les autres
                result = None

            def deliver(cb=callback, r=result, g=gen):
                if g == self.generation:
                    cb(r)
                return False
            GLib.idle_add(deliver)
