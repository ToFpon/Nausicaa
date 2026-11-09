"""Le moteur : libmpv, piloté depuis la boucle GLib.

mpv prévient depuis SES fils (propriétés observées, événements) : chaque valeur est rangée puis
remise à la boucle GLib par un seul idle_add en attente à la fois (une rafale de time-pos ne
déborde pas la boucle). Les écouteurs ne sont donc appelés que dans le fil principal.

Image : vo="libmpv", dessinée par ui/video.py (Gtk.GLArea + API de rendu OpenGL de mpv).
Sans interface (tests) : Player(video=False) → vo=null.

keep_open : à la fin d'un fichier, mpv garde la dernière image et passe eof-reached à vrai ; c'est
nous qui enchaînons (file, répétition, reprise), pas la liste interne de mpv (jamais utilisée).
"""

from __future__ import annotations

import locale
import threading
from typing import Callable, Optional

from gi.repository import GLib

import mpv

OBSERVED = ("pause", "time-pos", "duration", "eof-reached", "volume", "mute", "track-list",
            "media-title", "vid", "dwidth", "dheight", "seekable", "paused-for-cache", "chapter-list",
            "sub-visibility", "speed")


class Player:
    def __init__(self, video: bool = True, audio_output: str = "", **options):
        # mpv refuse de démarrer si les nombres ne s'écrivent pas avec un point (LC_NUMERIC=fr…)
        locale.setlocale(locale.LC_NUMERIC, "C")
        opts = dict(vo="libmpv" if video else "null", keep_open="yes", idle="yes",
                    input_default_bindings=False, input_vo_keyboard=False, osc=False, osd_level=0,
                    sub_auto="fuzzy", audio_file_auto="fuzzy", ytdl=False,
                    screenshot_format="png", hr_seek="yes", force_seekable="yes",
                    # une seule voix pour tout le volume (volume du système inchangé)
                    volume_max=150)
        if audio_output:
            opts["ao"] = audio_output
        opts.update(options)
        self.mpv = mpv.MPV(**opts)
        self.values: dict = {}
        self._pending: dict = {}
        self._lock = threading.Lock()
        self._scheduled = False
        self._listeners: list[Callable] = []
        self.path: Optional[str] = None          # fichier chargé (None : rien)
        self._closed = False
        for name in OBSERVED:
            self.mpv.observe_property(name, self._on_property)
        self.mpv.register_event_callback(self._on_event)

    # -- écouteurs ----------------------------------------------------------------------------
    def connect(self, cb: Callable):
        """cb(name, value) : name = propriété de mpv, ou « end-file » (raison, erreur), « file-loaded »."""
        self._listeners.append(cb)

    def _post(self, name, value):
        with self._lock:
            self._pending[name] = value
            if self._scheduled:
                return
            self._scheduled = True
        GLib.idle_add(self._flush)

    def _flush(self):
        with self._lock:
            pending, self._pending, self._scheduled = self._pending, {}, False
        if self._closed:
            return False
        # ordre stable : fin de fichier après les propriétés qui l'accompagnent
        for name in sorted(pending, key=lambda n: n in ("end-file", "file-loaded")):
            value = pending[name]
            if name not in ("end-file", "file-loaded"):
                self.values[name] = value
            for cb in list(self._listeners):
                cb(name, value)
        return False

    def _on_property(self, name, value):
        self._post(name, value)

    def _on_event(self, event):
        eid = event.event_id.value
        if eid == mpv.MpvEventID.END_FILE:
            data = event.data
            self._post("end-file", (data.reason, data.error))
        elif eid == mpv.MpvEventID.FILE_LOADED:
            self._post("file-loaded", True)

    # -- état ---------------------------------------------------------------------------------
    def get(self, name, default=None):
        v = self.values.get(name)
        return default if v is None else v

    @property
    def paused(self) -> bool:
        return bool(self.values.get("pause", True))

    @property
    def playing(self) -> bool:
        return self.path is not None and not self.paused and not self.values.get("eof-reached")

    def position(self) -> float:
        return float(self.values.get("time-pos") or 0.0)

    def duration(self) -> float:
        return float(self.values.get("duration") or 0.0)

    def tracks(self, kind: str) -> list[dict]:
        """Pistes « audio », « video » ou « sub » (dictionnaires de track-list de mpv)."""
        return [t for t in (self.values.get("track-list") or []) if t.get("type") == kind]

    # -- commandes ----------------------------------------------------------------------------
    def load(self, path: str, play: bool = True, start: float = 0.0):
        self.path = path
        self.values["eof-reached"] = False
        self.values["time-pos"] = start
        self.mpv.pause = not play
        args = dict(url=path, flags="replace")
        if start > 0:
            args["options"] = f"start={start:.3f}"     # chaîne « clé=valeur » : un dictionnaire est refusé
        # arguments nommés : même appel pour mpv 0.37 (Ubuntu 24.04) et 0.38+ (index ajouté avant options)
        self.mpv.command("loadfile", **args)

    def stop(self):
        self.path = None
        self.mpv.command("stop")

    def set_paused(self, value: bool):
        if self.path is None:
            return
        if not value and self.values.get("eof-reached"):
            self.seek(0, "absolute")              # relancer une vidéo finie : depuis le début
            self.values["eof-reached"] = False
        # valeur notée tout de suite : mpv ne la confirme qu'un peu plus tard (autre fil), et deux
        # bascules rapprochées (clic puis double-clic) doivent voir la première
        self.values["pause"] = bool(value)
        self.mpv.pause = bool(value)

    def toggle(self):
        # vidéo finie : toujours « relancer » (mpv annonce sa pause de fin un peu après eof-reached)
        self.set_paused(False if self.values.get("eof-reached") else not self.paused)

    def seek(self, seconds: float, reference: str = "relative"):
        if self.path is None:
            return
        try:
            self.mpv.command("seek", str(seconds), reference + "+exact")
        except SystemError:
            pass                                   # pas encore chargé : sans importance

    def seek_to(self, seconds: float):
        self.seek(max(0.0, seconds), "absolute")

    def frame_step(self, back: bool = False):
        if self.path is not None:
            self.mpv.command("frame-back-step" if back else "frame-step")

    def set_volume(self, volume: float, muted: bool):
        self.mpv.volume = max(0.0, min(150.0, float(volume)))
        self.mpv.mute = bool(muted)

    def set_property(self, name: str, value):
        try:
            self.mpv[name] = value
        except (SystemError, TypeError, AttributeError):
            pass

    def select_track(self, kind: str, track_id):
        """kind : « aid », « sid » ; track_id : numéro de piste ou « no »."""
        self.set_property(kind, track_id)

    def add_subtitle(self, path: str):
        self.mpv.command("sub-add", path, "select")

    def screenshot(self, path: str):
        self.mpv.command("screenshot-to-file", path, "subtitles")

    def shutdown(self):
        """À appeler APRÈS la libération du contexte de rendu (fenêtre détruite), sinon mpv avorte."""
        if self._closed:
            return
        self._closed = True
        self._listeners.clear()
        try:
            self.mpv.terminate()
        except Exception:  # noqa: BLE001 — déjà arrêté
            pass
