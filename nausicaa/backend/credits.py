"""Générique de fin : où il commence, pour proposer « Épisode suivant » (ou y passer tout seul).

Deux sources, sans Internet (choix de Tof) :
1. Les CHAPITRES du fichier : beaucoup de MKV (Blu-ray, plateformes) ont un chapitre « Credits », « Ending »,
   « Générique »… — repris de l'idée du script mpv « chapterskip ». Seulement dans la seconde moitié de la
   vidéo (un « Credits » du début est le générique d'ouverture). S'il reste après lui un chapitre qui n'est
   pas du générique (scène après le générique), on y saute au lieu de passer à l'épisode suivant.
2. La durée APPRISE pour la série : « Le générique commence ici » une fois, et Nausicaa retient « les X
   dernières secondes » pour toute la série (les génériques d'une série ont presque toujours la même durée).
   Série = dossier de la vidéo ; un dossier de saison (« Toto saison 2 », « Season 02 », « S03 ») compte pour
   la série entière. Rangé dans ~/.local/share/nausicaa/credits.json.

Les chapitres passent avant la durée apprise : ils sont exacts, épisode par épisode.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

from ..settings import DATA_DIR
from .library import name_key, season_of

CREDITS_FILE = os.path.join(DATA_DIR, "credits.json")
MIN_CREDITS = 5.0            # secondes : en deçà, ce n'est pas un générique (clic trop tard)
MAX_CREDITS = 15 * 60.0      # au-delà, ce n'en est pas un non plus
MIN_DURATION = 5 * 60.0      # clips courts : rien à passer

# titres de chapitres qui sont du générique de fin (ou ce qui le suit : l'aperçu du prochain épisode)
_CREDITS = re.compile(
    r"(?i)^\s*(?:\d+[\s.:-]*)?(?:end(?:ing)?[\s_-]*credits?|credits?|ending|outro|ed\d*|end|"
    r"g[ée]n[ée]rique(?:\s+de\s+fin)?|fin|abspann|nachspann|cr[ée]ditos(?:\s+finales)?|titoli\s+di\s+coda|"
    r"(?:next\s+(?:episode\s+)?)?preview|aper[çc]u|bande[\s-]annonce)\s*$")


def is_credits_title(title) -> bool:
    return isinstance(title, str) and bool(_CREDITS.match(title))


@dataclass
class Credits:
    start: float                     # secondes : le générique commence ici
    resume: Optional[float] = None   # scène après le générique : y aller ; None = épisode suivant
    source: str = "chapters"         # chapters, learned


def from_chapters(chapters, duration: float) -> Optional[Credits]:
    """Générique d'après les chapitres de mpv ([{"title", "time"}…]), ou None."""
    if not chapters or not duration or duration < MIN_DURATION:
        return None
    marks = []
    for c in chapters:
        if isinstance(c, dict) and isinstance(c.get("time"), (int, float)):
            marks.append((float(c["time"]), c.get("title")))
    marks.sort(key=lambda m: m[0])
    for i, (t, title) in enumerate(marks):
        if t < duration / 2 or not is_credits_title(title):
            continue
        rest = [m for m in marks[i + 1:] if not is_credits_title(m[1])]
        resume = rest[0][0] if rest and rest[0][0] < duration - 1 else None
        if duration - t < MIN_CREDITS:
            return None
        return Credits(t, resume, "chapters")
    return None


def series_key(path: str) -> Optional[str]:
    """Clé de la série d'une vidéo : son dossier, ou la série entière si c'est un dossier de saison."""
    if not path or "://" in path:
        return None
    folder = os.path.dirname(os.path.abspath(path))
    name = os.path.basename(folder)
    show, number = season_of(name)
    if number is not None:
        name = show or os.path.basename(os.path.dirname(folder))
    key = name_key(name)
    return key or None


def series_name(path: str) -> str:
    """Nom lisible de la série (pour les messages)."""
    folder = os.path.dirname(os.path.abspath(path))
    name = os.path.basename(folder)
    show, number = season_of(name)
    if number is not None:
        name = show or os.path.basename(os.path.dirname(folder))
    return name


class CreditsStore:
    """Durées de générique apprises : {clé de série: secondes avant la fin}."""

    def __init__(self, path: str = CREDITS_FILE):
        self.path = path
        self.data: dict = {}
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data = {k: float(v) for k, v in loaded.items()
                             if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
                             and MIN_CREDITS <= v <= MAX_CREDITS}
        except (OSError, ValueError):
            pass

    def get(self, path: str) -> Optional[float]:
        key = series_key(path)
        return self.data.get(key) if key else None

    def learn(self, path: str, position: float, duration: float) -> Optional[float]:
        """« Le générique commence ici » : retient la durée qui reste. None si ça n'a pas de sens (début de la
        vidéo, reste trop court ou trop long, flux)."""
        key = series_key(path)
        left = (duration or 0) - (position or 0)
        if key is None or duration < MIN_DURATION or position < duration / 2 or not (MIN_CREDITS <= left <= MAX_CREDITS):
            return None
        self.data[key] = round(left, 1)
        self.save()
        return self.data[key]

    def forget(self, path: str) -> Optional[float]:
        key = series_key(path)
        old = self.data.pop(key, None) if key else None
        if old is not None:
            self.save()
        return old

    def put_back(self, path: str, value: Optional[float]):
        """« Annuler » : la valeur d'avant (None : rien)."""
        key = series_key(path)
        if key is None:
            return
        if value is None:
            self.data.pop(key, None)
        else:
            self.data[key] = value
        self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=1, ensure_ascii=True)
            os.replace(tmp, self.path)
        except OSError:
            pass


def find(path: str, duration: float, chapters, store: CreditsStore) -> Optional[Credits]:
    """Le générique de cette vidéo : d'après ses chapitres, sinon la durée apprise pour sa série."""
    found = from_chapters(chapters, duration)
    if found is not None:
        return found
    left = store.get(path)
    if left and duration >= MIN_DURATION and duration - left > duration / 2:
        return Credits(duration - left, None, "learned")
    return None
