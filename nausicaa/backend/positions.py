"""Positions de reprise : où on s'était arrêté dans chaque vidéo.

Clé = chemin réel + taille (un fichier remplacé par un autre du même nom repart du début). Rien
n'est retenu au tout début (moins de MIN_POSITION) ni à la fin (vidéo vue : entrée effacée). Les
MAX_ENTRIES plus récentes sont gardées, dans ~/.local/share/nausicaa/positions.json.
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
MAX_ENTRIES = 1000


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

    def remember(self, path: str, position: float, duration: float) -> bool:
        """Retient (ou efface) la position ; renvoie True si quelque chose a changé."""
        k = key_for(path)
        if not k:
            return False
        if (not duration or duration < MIN_DURATION or position < MIN_POSITION
                or position > duration - max(END_SECONDS, duration * END_MARGIN)):
            return self.data.pop(k, None) is not None
        self.data[k] = {"pos": round(position, 1), "time": int(time.time())}
        return True

    def forget(self, path: str):
        k = key_for(path)
        if k:
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
