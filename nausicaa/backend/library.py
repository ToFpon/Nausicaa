"""Bibliothèque de l'accueil (fonctionnalité « library ») : les vidéos des dossiers choisis, rangées en
séries et en films, sans base de données — relues à chaque affichage de l'accueil (dans un fil).

Séries (rangement de Tof) : un dossier par saison, « Toto saison 1 », « Toto saison 2 »… regroupés en
une série « Toto ». Aussi « Toto/Saison 1 », « Toto - Season 02 », « Toto S03 », et un dossier sans saison
dont les vidéos sont des épisodes (« S01E02 », « 1x02 », « Épisode 3 », numéros). Films : vidéos à la
racine d'un dossier de la bibliothèque, ou seules dans leur dossier (« Film (2019)/Film.mkv »), ou dans un
dossier qui ne ressemble pas à une série.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from . import media

MAX_DEPTH = 6

# « Toto saison 1 », « Toto - Season 02 », « Toto (Saison 3) », « Toto S04 », « Staffel 5 » ; seul (« Saison 1 »)
_SEASON = re.compile(r"^(?P<name>.*?)[\s._\-–(\[]*(?:saison|season|staffel|temporada|stagione|seizoen)"
                     r"[\s._-]*(?P<num>\d{1,3})[)\]]?(?P<rest>.*)$", re.IGNORECASE)
_SEASON_S = re.compile(r"^(?P<name>.*?)[\s._\-–(\[]+S(?P<num>\d{1,2})[)\]]?(?P<rest>(?:[\s._-].*)?)$",
                       re.IGNORECASE)
_SEASON_ALONE = re.compile(r"^S(?P<num>\d{1,2})$", re.IGNORECASE)
# noms d'épisodes
_EPISODE = re.compile(r"(?i)(?:\bs\d{1,2}[\s._-]?e\d{1,3}\b|\b\d{1,2}x\d{2,3}\b|[ée]pisode|\bep[\s._-]?\d|"
                      r"\be\d{2,3}\b|\bfolge\b)")
_NUMBER = re.compile(r"(?<![\d])\d{1,3}(?![\d])")
_EPISODE_NUM = re.compile(r"(?i)(?:s(?P<s>\d{1,2})[\s._-]?e(?P<e>\d{1,3})|\b(?P<s2>\d{1,2})x(?P<e2>\d{2,3})\b)")
# ce qui suit le titre dans un nom de fichier « de sortie » (« Film.2019.1080p.BluRay.x264-GRP »)
_JUNK = re.compile(r"(?i)[\s._\-\[(]+(?:\d{3,4}p|4k|uhd|hdr\d*|x26[45]|h\.?26[45]|hevc|avc|bluray|blu-ray|bdrip|"
                   r"brrip|web-?dl|web-?rip|webrip|hdtv|dvdrip|hdrip|xvid|divx|aac\d?|ac3|dts|truefrench|french|"
                   r"vostfr|vff|vfq|multi|subfrench|proper|repack|remux|10bit|imax)\b.*$")
_YEAR = re.compile(r"[\s._\-\[(]+\(?((?:19|20)\d\d)\)?(?=[\s._\-\])]|$)")


@dataclass
class Season:
    number: Optional[int]                   # None : série sans numéro de saison
    folder: str
    episodes: list = field(default_factory=list)       # chemins, ordre naturel

    @property
    def label(self) -> str:
        return os.path.basename(self.folder)


@dataclass
class Show:
    name: str
    seasons: list = field(default_factory=list)        # Season, triées
    folder: str = ""                                    # dossier de la série s'il existe (« Toto/Saison 1 »)

    @property
    def episodes(self) -> list:
        return [e for s in self.seasons for e in s.episodes]

    @property
    def folders(self) -> list:
        """Où chercher une affiche : dossier de la série, puis chaque saison."""
        return ([self.folder] if self.folder else []) + [s.folder for s in self.seasons]


@dataclass
class Library:
    films: list = field(default_factory=list)          # chemins
    shows: list = field(default_factory=list)          # Show, par nom

    @property
    def empty(self) -> bool:
        return not self.films and not self.shows


def season_of(folder_name: str) -> tuple[str, Optional[int]]:
    """(nom de la série, numéro) d'un nom de dossier de saison ; ("", None) si ce n'en est pas un.
    Le nom est vide pour « Saison 2 » seul (la série est alors le dossier parent)."""
    name = folder_name.strip()
    m = _SEASON_ALONE.match(name)
    if m:
        return "", int(m.group("num"))
    for rx in (_SEASON, _SEASON_S):
        m = rx.match(name)
        if m:
            return _tidy(m.group("name")), int(m.group("num"))
    return "", None


def _tidy(name: str) -> str:
    name = name.strip(" ._-–([")
    if " " not in name and ("." in name or "_" in name):
        name = name.replace(".", " ").replace("_", " ")
    return re.sub(r"\s+", " ", name).strip()


def clean_title(path: str) -> str:
    """Titre lisible d'un fichier vidéo : « Mon.Film.2019.1080p.BluRay.x264-GRP.mkv » → « Mon Film (2019) »."""
    stem = os.path.splitext(os.path.basename(path))[0]
    year = ""
    cut = _JUNK.sub("", stem)
    years = [m for m in _YEAR.finditer(cut) if m.start() > 0]
    if years:                                    # la dernière : « Blade Runner 2049 (2017) »
        year = years[-1].group(1)
        cut = cut[:years[-1].start()]
    title = _tidy(cut) or stem
    return f"{title} ({year})" if year else title


def episode_label(path: str) -> str:
    """« S01E03 » d'un nom d'épisode, "" s'il n'en a pas."""
    m = _EPISODE_NUM.search(os.path.basename(path))
    if not m:
        return ""
    s, e = (m.group("s"), m.group("e")) if m.group("s") else (m.group("s2"), m.group("e2"))
    return f"S{int(s):02d}E{int(e):02d}"


def _looks_like_episodes(videos: list) -> bool:
    if len(videos) < 2:
        return False
    names = [os.path.splitext(os.path.basename(v))[0] for v in videos]
    marked = sum(1 for n in names if _EPISODE.search(n))
    if marked * 2 >= len(names):
        return True
    numbered = sum(1 for n in names if _NUMBER.search(re.sub(r"(?:19|20)\d\d", "", n)))
    return numbered * 3 >= len(names) * 2


def _videos(folder: str) -> tuple[list, list]:
    """(vidéos, sous-dossiers) d'un dossier, en ordre naturel, cachés ignorés, liens de dossiers non suivis."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return [], []
    files, dirs = [], []
    for e in entries:
        if e.name.startswith("."):
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                dirs.append(e.name)
            elif e.is_file() and media.is_video(e.name):
                files.append(e.name)
        except OSError:
            continue
    files.sort(key=media.name_key)
    dirs.sort(key=media.name_key)
    return [os.path.join(folder, f) for f in files], [os.path.join(folder, d) for d in dirs]


def scan(roots: list, max_depth: int = MAX_DEPTH) -> Library:
    """Lit les dossiers de la bibliothèque (à appeler dans un fil : disque lent, partage réseau)."""
    films: list = []
    shows: dict = {}              # nom en minuscules → Show
    seen_dirs: set = set()

    def show_for(name: str, folder: str = "") -> Show:
        key = name_key(name)
        s = shows.get(key)
        if s is None:
            s = shows[key] = Show(name, folder=folder)
        elif folder and not s.folder:
            s.folder = folder
        return s

    def walk(folder: str, depth: int, is_root: bool):
        real = os.path.realpath(folder)
        if real in seen_dirs or depth > max_depth:
            return
        seen_dirs.add(real)
        videos, subs = _videos(folder)
        if is_root:
            films.extend(videos)
        elif videos:
            base = os.path.basename(folder)
            name, num = season_of(base)
            parent = os.path.dirname(folder)
            if num is not None and not name:          # « Toto/Saison 1 »
                show_for(os.path.basename(parent) if parent and parent not in roots else base,
                         parent if parent not in roots else "").seasons.append(Season(num, folder, videos))
            elif num is not None:                     # « Toto saison 1 »
                show_for(name).seasons.append(Season(num, folder, videos))
            elif _looks_like_episodes(videos):       # « Toto/ » plein d'épisodes
                show_for(_tidy(base), folder).seasons.append(Season(None, folder, videos))
            else:
                films.extend(videos)
        for d in subs:
            walk(d, depth + 1, False)

    roots = [os.path.abspath(r) for r in roots if r]
    for r in roots:
        if os.path.isdir(r):
            walk(r, 0, True)
    for s in shows.values():
        s.seasons.sort(key=lambda x: (x.number is None, x.number or 0, media.name_key(x.label)))
    films.sort(key=lambda p: media.name_key(clean_title(p)))
    return Library(films=films, shows=sorted(shows.values(), key=lambda s: media.name_key(s.name)))


def name_key(name: str) -> str:
    """Nom d'une série pour regrouper ses saisons : casse, accents gardés, ponctuation ignorée — « Hunting :
    Chasseurs » et « Hunting - Chasseurs » (un « : » remplacé à la copie sur une clé) sont la même série."""
    return " ".join(re.sub(r"[\W_]+", " ", name.casefold()).split())


def default_roots() -> list:
    """Dossier Vidéos de l'utilisateur (xdg-user-dirs), sinon ~/Vidéos ou ~/Videos."""
    try:
        from gi.repository import GLib
        d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_VIDEOS)
    except Exception:  # noqa: BLE001
        d = None
    if d and os.path.isdir(d) and os.path.abspath(d) != os.path.expanduser("~"):
        return [d]
    for name in ("Vidéos", "Videos"):
        p = os.path.expanduser("~/" + name)
        if os.path.isdir(p):
            return [p]
    return []
