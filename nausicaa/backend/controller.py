"""Le lecteur et sa file ensemble (boucle GLib, sans GTK : testable seul).

Événements envoyés aux écouteurs (cb(event, *args)) : item (élément courant changé), state (lecture /
pause), position, duration, queue (contenu ou ordre de la file), modes (aléatoire, répétition),
volume, tracks (pistes audio / sous-titres), video (taille de l'image, présence d'une image),
title, resumed (position de reprise), ended (fin de la file), error (élément, message), loading.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from gi.repository import GLib

from .. import features
from ..i18n import tr
from ..settings import settings
from . import media
from .media import Item
from .player import Player
from .playqueue import PlayQueue, load_saved, restore
from .positions import Positions

RESTART_AFTER = 3.0          # « Précédent » après 3 s : retour au début de la vidéo
SAVE_EVERY_MS = 10000


class Controller:
    def __init__(self, player: Optional[Player] = None, queue_file: str = "",
                 positions: Optional[Positions] = None):
        self.player = player or Player(**self._mpv_options())
        self.player.connect(self._on_player)
        self.queue = PlayQueue()
        self.queue.repeat = settings["repeat"]
        self.queue.shuffled = bool(settings["shuffle"])
        self.queue.connect(self._on_queue)
        self.positions = positions or Positions()
        self.queue_file = queue_file
        self.loading = 0
        self._errors = 0
        self._listeners: list[Callable] = []
        self._save_timer = 0
        self._ended_path: Optional[str] = None
        self._apply_volume()
        self._autosave = GLib.timeout_add(SAVE_EVERY_MS, self._periodic_save)
        settings.connect(self._on_setting)

    @staticmethod
    def _mpv_options() -> dict:
        opts = {"hwdec": "auto-safe" if features.enabled("hwdec") else "no"}
        if os.environ.get("NAUSICAA_AO"):              # tests : sortie son factice (« null »)
            opts["ao"] = os.environ["NAUSICAA_AO"]
        if settings["audio_language"]:
            opts["alang"] = settings["audio_language"]
        if settings["subtitle_language"]:
            opts["slang"] = settings["subtitle_language"]
        return opts

    # -- écouteurs --------------------------------------------------------------------------
    def connect(self, cb: Callable):
        self._listeners.append(cb)

    def disconnect(self, cb):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _emit(self, event: str, *args):
        for cb in list(self._listeners):
            cb(event, *args)

    @property
    def item(self) -> Optional[Item]:
        return self.queue.current

    @property
    def playing(self) -> bool:
        return self.player.playing

    # -- ouverture --------------------------------------------------------------------------
    def open_paths(self, paths: list[str], replace: bool = True, play: bool = True,
                   on_done: Optional[Callable] = None):
        """Ouvre fichiers, dossiers, listes de lecture, adresses (lus dans un fil).

        replace : la file est remplacée (ouvrir) ; sinon ajout à la fin (« Ajouter à la file »).
        Une seule vidéo ouverte, « Épisodes suivants » actif : les vidéos de son dossier viennent
        avec elle, la lecture commence par elle."""
        siblings = (replace and len(paths) == 1 and os.path.isfile(paths[0]) and media.is_media(paths[0])
                    and features.enabled("next_episodes"))
        self.loading += 1
        self._emit("loading")

        def work():
            titles: dict = {}
            if siblings:
                found, first = media.siblings(paths[0]), os.path.abspath(paths[0])
            else:
                found = media.expand(paths, titles)
                first = found[0] if found else None
            GLib.idle_add(lambda: (done(found, first, titles), False)[1])

        def done(found, first, titles):
            self.loading -= 1
            items = [Item(p, titles.get(p, "")) for p in found]
            if replace:
                self.stop()
                self.queue.clear()
            new = self.queue.add(items)
            start = next((i for i in new if i.path == first), new[0] if new else None)
            if start is not None and (play or self.queue.current is None):
                self.play_item(start, play=play)
            self._emit("loading")
            self.save_soon()
            if on_done:
                on_done(len(new))
        threading.Thread(target=work, daemon=True).start()

    def restore_saved(self, play: bool = True) -> bool:
        """Remet la dernière file, sur l'élément où on l'avait laissée (la reprise fait le reste)."""
        data = load_saved(self.queue_file) if self.queue_file else load_saved()
        if data is None:
            return False
        items = [Item(p, data.get("titles", {}).get(p, "")) for p in data["songs"]]
        restore(self.queue, data, items)
        settings["shuffle"] = self.queue.shuffled
        settings["repeat"] = self.queue.repeat
        if self.queue.current is not None:
            self.play_item(self.queue.current, play=play)
        return True

    @staticmethod
    def saved_queue(queue_file: str = "") -> Optional[dict]:
        return load_saved(queue_file) if queue_file else load_saved()

    # -- commandes --------------------------------------------------------------------------
    def play_item(self, item: Item, play: bool = True, from_start: bool = False):
        if item not in self.queue.items:
            return
        self._remember_position()
        self.queue.set_current(self.queue.items.index(item))
        start = 0.0
        if features.enabled("resume") and not from_start:
            start = self.positions.get(item.path)
        self._ended_path = None
        self.player.load(item.path, play=play, start=start)
        if start:
            self._emit("resumed", start)

    def play_index(self, index: int):
        if 0 <= index < len(self.queue):
            self.play_item(self.queue.items[index])

    def toggle(self):
        if self.player.path is None:
            if self.queue.current is not None:
                self.play_item(self.queue.current)
            elif len(self.queue):
                self.play_index(0)
            return
        self.player.toggle()

    def set_paused(self, value: bool):
        if self.player.path is None:
            if not value:
                self.toggle()
            return
        self.player.set_paused(value)
        if value:
            self._remember_position(save=True)

    def next(self):
        i = self.queue.next_index()
        if self.queue.repeat == "one" and len(self.queue) > 1:       # « Suivant » change de vidéo
            i = (self.queue.pos + 1) % len(self.queue)
        if i is not None:
            self.play_item(self.queue.items[i], play=not self.player.paused or self.player.path is None)

    def previous(self):
        if self.player.position() > RESTART_AFTER:
            self.player.seek_to(0)
            return
        i = self.queue.previous_index()
        if i is not None:
            self.play_item(self.queue.items[i], play=not self.player.paused or self.player.path is None)

    def restart(self):
        """« Depuis le début » (après une reprise)."""
        if self.item is not None:
            self.positions.forget(self.item.path)
            self.player.seek_to(0)

    def seek(self, seconds: float):
        self.player.seek(seconds)

    def seek_to(self, seconds: float):
        self.player.seek_to(seconds)

    def remove(self, items: list[Item]):
        was_current = self.queue.current
        self.queue.remove(items)
        if was_current is not None and was_current in items:
            self._remember_position(save=True, item=was_current)
            if self.queue.current is not None:
                self.play_item(self.queue.current, play=not self.player.paused)
            else:
                self.stop()
        self.save_soon()

    def clear(self):
        self.stop()
        self.queue.clear()
        self.save_soon()

    def stop(self):
        self._remember_position(save=True)
        if self.player.path is not None:
            self.player.stop()
        self._emit("state")

    def move(self, item: Item, index: int):
        self.queue.move(item, index)

    def set_shuffled(self, value: bool):
        self.queue.set_shuffled(value)
        settings["shuffle"] = value

    def cycle_repeat(self) -> str:
        mode = self.queue.cycle_repeat()
        settings["repeat"] = mode
        return mode

    def set_volume(self, volume: float):
        settings["volume"] = int(round(max(0.0, min(float(settings["volume_max"]), volume))))
        if settings["muted"] and volume > 0:
            settings["muted"] = False

    def change_volume(self, delta: float):
        self.set_volume(settings["volume"] + delta)

    def set_muted(self, muted: bool):
        settings["muted"] = muted

    # -- réactions --------------------------------------------------------------------------
    def _apply_volume(self):
        vol = min(settings["volume"], settings["volume_max"])
        self.player.set_volume(vol, settings["muted"])

    def _on_setting(self, key, value):
        if key == "volume_max" and settings["volume"] > value:
            settings["volume"] = value                # plus d'amplification : retour à 100 %
            return
        if key in ("volume", "muted", "volume_max"):
            self._apply_volume()
            self._emit("volume")
        elif key == "features":
            self.player.set_property("hwdec", "auto-safe" if features.enabled("hwdec") else "no")
        elif key == "audio_language":
            self.player.set_property("alang", value)
        elif key == "subtitle_language":
            self.player.set_property("slang", value)

    def _on_queue(self, what: str):
        if what == "items":
            self._emit("queue")
            self.save_soon()
        elif what == "current":
            self._emit("item")
            self.save_soon()
        elif what == "modes":
            self._emit("modes")

    def _on_player(self, name: str, value):
        if name == "pause":
            self._emit("state")
        elif name == "time-pos":
            self._emit("position", float(value or 0.0))
        elif name == "duration":
            if value and self.item is not None:
                self.item.duration = float(value)
            self._emit("duration", float(value or 0.0))
        elif name == "eof-reached":
            if value and self.player.path is not None:
                self._on_eof()
            self._emit("state")
        elif name == "track-list":
            self._emit("tracks")
        elif name in ("vid", "dwidth", "dheight"):
            self._emit("video")
        elif name == "media-title":
            self._emit("title")
        elif name in ("volume", "mute"):
            pass                                     # c'est nous qui les réglons
        elif name == "file-loaded":
            self._errors = 0
            self._emit("video")
        elif name == "end-file":
            reason, error = value
            if reason == 4 and self.player.path is not None:      # MPV_END_FILE_REASON_ERROR
                self._on_error(error)

    def _on_eof(self):
        """Fin d'une vidéo : vue (position oubliée), puis la suivante selon la répétition."""
        path = self.player.path
        if self._ended_path == path:
            return
        self._ended_path = path
        if self.item is not None:
            self.positions.forget(self.item.path)
            self.positions.save()
        if self.queue.repeat == "one":
            self.player.set_paused(False)
            return
        i = self.queue.next_index()
        if i is None:
            self._emit("ended")
            return
        self.play_item(self.queue.items[i])

    def _on_error(self, code: int):
        item = self.item
        message = tr("Ce fichier ne peut pas être lu.")
        if item is not None and not item.stream and not os.path.exists(item.path):
            message = tr("Le fichier n'existe plus.")
        self.player.path = None
        self._emit("error", item, message)
        self._errors += 1
        if self._errors >= len(self.queue):          # tout est illisible : on s'arrête
            self._emit("state")
            return
        i = self.queue.next_index()
        if i is not None and self.queue.items[i] is not item:
            self.play_item(self.queue.items[i])
        else:
            self._emit("state")

    # -- enregistrement ---------------------------------------------------------------------
    def _remember_position(self, save: bool = False, item: Optional[Item] = None):
        item = item or self.item
        if item is None or self.player.path != item.path or self._ended_path == item.path:
            return
        if not features.enabled("resume"):
            return
        changed = self.positions.remember(item.path, self.player.position(), self.player.duration())
        if save and changed:
            self.positions.save()

    def save(self):
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save_timer = 0
        args = (self.player.position(),) + ((self.queue_file,) if self.queue_file else ())
        self.queue.save(*args)

    def save_soon(self):
        if not self._save_timer:
            self._save_timer = GLib.timeout_add(700, self._save_timeout)

    def _save_timeout(self):
        self._save_timer = 0
        self.save()
        return False

    def _periodic_save(self):
        if self.player.playing:
            self._remember_position(save=True)
            self.save()
        return True

    def shutdown(self, terminate: bool = True):
        """terminate=False : la fenêtre libère d'abord son contexte de rendu, puis player.shutdown()."""
        self._remember_position(save=True)
        self.save()
        if self._autosave:
            GLib.source_remove(self._autosave)
            self._autosave = 0
        settings.disconnect(self._on_setting)
        if terminate:
            self.player.shutdown()
