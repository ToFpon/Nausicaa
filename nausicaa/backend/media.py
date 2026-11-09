"""Médias de la file : vidéos (et sons), dossiers parcourus en ordre naturel, listes de lecture dépliées.

Pas de GTK ici : testable seul.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from dataclasses import dataclass
from typing import Optional

from . import playlists

VIDEO_EXTS = (".mkv", ".mp4", ".m4v", ".avi", ".webm", ".mov", ".wmv", ".flv", ".mpg", ".mpeg", ".m2ts",
              ".mts", ".ts", ".vob", ".ogv", ".3gp", ".3g2", ".divx", ".xvid", ".rmvb", ".rm", ".asf",
              ".f4v", ".mxf", ".y4m", ".dv")
AUDIO_EXTS = (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".aac", ".wav", ".wv", ".ape",
              ".wma", ".mka", ".ac3", ".dts", ".aiff", ".aif")
SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup")
MEDIA_EXTS = VIDEO_EXTS + AUDIO_EXTS


@dataclass(eq=False)
class Item:
    """Un élément de la file. eq=False : deux éléments ne sont égaux que s'ils sont le même objet."""
    path: str                       # chemin local absolu, ou adresse d'un flux
    title: str = ""                 # titre donné par une liste de lecture ou par le flux
    duration: float = 0.0           # connue une fois lue (ou donnée par la liste)

    @property
    def stream(self) -> bool:
        return "://" in self.path

    @property
    def display_title(self) -> str:
        if self.title:
            return self.title
        if self.stream:
            return self.path
        return os.path.splitext(os.path.basename(self.path))[0]


def is_video(path: str) -> bool:
    return path.lower().endswith(VIDEO_EXTS)


def is_media(path: str) -> bool:
    return path.lower().endswith(MEDIA_EXTS)


def is_subtitle(path: str) -> bool:
    return path.lower().endswith(SUBTITLE_EXTS)


# -- ordre naturel des noms, comme Nautilus (repris d'Odysseus, backend/scanner.py) ---------------
def _glib_collate():
    lib = ctypes.CDLL(ctypes.util.find_library("glib-2.0") or "libglib-2.0.so.0")
    fn = lib.g_utf8_collate_key_for_filename
    fn.argtypes, fn.restype = [ctypes.c_char_p, ctypes.c_ssize_t], ctypes.c_void_p
    free = lib.g_free
    free.argtypes, free.restype = [ctypes.c_void_p], None
    return fn, free


_collate_fn, _g_free = _glib_collate()


def name_key(name: str) -> bytes:
    """g_utf8_collate_key_for_filename en octets bruts (« Épisode 2 » avant « Épisode 10 »). Jamais par
    PyGObject : la clé vient de strxfrm et n'est pas toujours de l'UTF-8 (vu dans Odysseus)."""
    try:
        raw = name.encode("utf-8")
    except UnicodeEncodeError:
        raw = name.encode("utf-8", "surrogateescape").decode("utf-8", "replace").encode("utf-8")
    raw = raw.replace(b"\0", b"")
    ptr = _collate_fn(raw, -1)
    key = ctypes.string_at(ptr)
    _g_free(ptr)
    return key


def folder_media(folder: str) -> list[str]:
    """Médias d'un dossier (sans les sous-dossiers), en ordre naturel, cachés ignorés."""
    try:
        names = [e.name for e in os.scandir(folder) if not e.name.startswith(".") and e.is_file()]
    except OSError:
        return []
    return [os.path.join(folder, n) for n in sorted(names, key=name_key) if is_media(n)]


def expand(paths: list[str], titles: Optional[dict] = None) -> list[str]:
    """Médias des chemins donnés : dossiers parcourus (fichiers d'abord, puis sous-dossiers, en ordre
    naturel), listes de lecture dépliées (flux compris). titles reçoit le titre que la liste donne à
    un élément. Un fichier donné explicitement est gardé même si son extension est inconnue : mpv
    lit bien des choses."""
    out: list[str] = []
    seen: set = set()

    def add(p):
        key = p if "://" in p else os.path.realpath(p)
        if key not in seen:
            seen.add(key)
            out.append(p)

    def walk(folder, depth=0):
        if depth > 32:
            return
        for p in folder_media(folder):
            add(p)
        try:
            subs = sorted((e.name for e in os.scandir(folder)
                           if not e.name.startswith(".") and e.is_dir(follow_symlinks=False)), key=name_key)
        except OSError:
            return
        for d in subs:
            walk(os.path.join(folder, d), depth + 1)

    for p in paths:
        if playlists.is_stream(p):
            add(p)
        elif os.path.isfile(p) and playlists.is_playlist(p):
            for e in playlists.parse(p):
                if titles is not None and e.title:
                    titles.setdefault(e.location, e.title)
                if not playlists.is_stream(e.location) and os.path.isdir(e.location):
                    walk(e.location)
                else:
                    add(e.location)          # absent : signalé à la lecture
        elif os.path.isdir(p):
            walk(p)
        elif os.path.isfile(p):
            add(os.path.abspath(p))
    return out


def siblings(path: str) -> list[str]:
    """« Épisodes suivants » : les vidéos du dossier d'une vidéo ouverte seule, en ordre naturel
    (elle comprise, à sa place). Un son ouvert seul amène les sons du dossier."""
    folder = os.path.dirname(os.path.abspath(path))
    video = is_video(path)
    found = [p for p in folder_media(folder) if is_video(p) == video]
    me = os.path.abspath(path)
    return found if me in found else [me]


def external_subtitles(path: str) -> list[str]:
    """Sous-titres posés à côté d'une vidéo (« Film.srt », « Film.fr.srt », « Film.forced.ass »…).
    mpv les trouve seul (sub-auto=fuzzy) ; sert aux tests et à l'affichage."""
    folder = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted((os.path.join(folder, n) for n in names
                   if is_subtitle(n) and n.lower().startswith(stem)), key=lambda p: name_key(os.path.basename(p)))
