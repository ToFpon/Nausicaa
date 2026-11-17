"""Positions de reprise : où on s'était arrêté dans chaque vidéo.

Clé = chemin réel + taille (un fichier remplacé par un autre du même nom repart du début). Rien
n'est retenu au tout début (moins de MIN_POSITION) ; à la fin, la vidéo est notée VUE (« seen »,
position remise à 0) — la bibliothèque de l'accueil en fait une coche. La durée (« dur ») sert à sa
barre d'avancement. Les MAX_ENTRIES plus récentes sont gardées, dans
~/.local/share/nausicaa/positions.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

from ..settings import DATA_DIR

POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
MIN_POSITION = 15.0         # secondes : en deçà, on repart du début
END_MARGIN = 0.05           # dernière partie (5 %, au moins END_SECONDS) : vidéo vue
END_SECONDS = 20.0
MIN_DURATION = 60.0         # clips courts : pas de reprise
MAX_ENTRIES = 5000          # (vues comprises : une vidéothèque entière)


def key_for(path: str) -> Optional[str]:
    if "://" in path:
        return None                       # flux : pas de reprise
    try:
        real = os.path.realpath(path)
        size = os.stat(real).st_size
    except OSError:
        return None
    return hashlib.sha1(f"{real}|{size}".encode("utf-8", "surrogateescape")).hexdigest()


class Positions:
    def __init__(self, path: str = POSITIONS_FILE):
        self.path = path
        self.data: dict = {}
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data = {k: v for k, v in loaded.items()
                             if isinstance(v, dict) and isinstance(v.get("pos"), (int, float))}
        except (OSError, ValueError):
            pass

    def get(self, path: str) -> float:
        """Position où reprendre (0 : depuis le début)."""
        k = key_for(path)
        entry = self.data.get(k) if k else None
        return float(entry["pos"]) if entry else 0.0

    def entry(self, key: Optional[str]) -> dict:
        """{pos, dur, seen, time} d'une clé (key_for), {} si rien n'est retenu."""
        return dict(self.data.get(key) or {}) if key else {}

    def remember(self, path: str, position: float, duration: float) -> bool:
        """Retient (ou efface) la position ; renvoie True si quelque chose a changé. Passé la fin : vue."""
        k = key_for(path)
        if not k:
            return False
        old = self.data.get(k)
        if not duration or duration < MIN_DURATION:
            if old and old.get("seen"):
                return False                        # clip court déjà vu : il le reste
            return self.data.pop(k, None) is not None
        if position > duration - max(END_SECONDS, duration * END_MARGIN):
            self.data[k] = {"pos": 0, "dur": round(duration, 1), "seen": True, "time": int(time.time())}
            return self.data[k] != old
        if position < MIN_POSITION:
            if old and old.get("seen"):
                new = dict(old, pos=0)              # revue depuis le début : reste vue
                self.data[k] = new
                return new != old
            return self.data.pop(k, None) is not None
        self.data[k] = {"pos": round(position, 1), "dur": round(duration, 1), "time": int(time.time())}
        if old and old.get("seen"):
            self.data[k]["seen"] = True
        return True

    def finished(self, path: str, duration: float = 0.0):
        """Vidéo lue jusqu'au bout : position oubliée, notée vue (clips courts compris)."""
        k = key_for(path)
        if k:
            old = self.data.get(k) or {}
            self.data[k] = {"pos": 0, "dur": round(duration or old.get("dur", 0), 1), "seen": True,
                            "time": int(time.time())}

    def hide(self, path: str, hidden: bool = True) -> bool:
        """« Retirer de Reprendre » (bibliothèque) : la vidéo quitte la liste mais garde sa position (la reprise
        marche toujours) ; regardée de nouveau, elle y revient (remember écrit une entrée neuve)."""
        k = key_for(path)
        old = self.data.get(k) if k else None
        if not old:
            return False
        new = dict(old)
        if hidden:
            new["hidden"] = True
        else:
            new.pop("hidden", None)
        self.data[k] = new
        return new != old

    def forget(self, path: str):
        """« Depuis le début » : position oubliée (une vidéo vue le reste)."""
        k = key_for(path)
        if not k:
            return
        old = self.data.get(k)
        if old and old.get("seen"):
            self.data[k] = dict(old, pos=0)
        else:
            self.data.pop(k, None)

    def save(self):
        if len(self.data) > MAX_ENTRIES:
            newest = sorted(self.data.items(), key=lambda kv: kv[1].get("time", 0), reverse=True)
            self.data = dict(newest[:MAX_ENTRIES])
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass
