"""Affiches de TMDB (themoviedb.org, fonctionnalité « tmdb », coupée par défaut ; demande de Tof) : films et
séries, dans la langue de Nausicaa. Avec la clé d'API de l'UTILISATEUR (choix de Tof : rien n'est lié à un
compte de l'application) — clé « v3 » (32 caractères, paramètre api_key) ou jeton de lecture « v4 » (eyJ…,
en-tête Authorization). Seuls les titres partent chez TMDB.

Recherche (search/tv ou search/movie, année d'un film comprise), puis images du résultat : affiche dans la
langue voulue d'abord, puis sans texte, puis la mieux notée. Réponses retenues dans ~/.cache/nausicaa/tmdb.json
(« pas trouvé » aussi, redemandé après 14 jours), images dans ~/.cache/nausicaa/tmdb/. À appeler dans un fil.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from ..settings import CACHE_DIR

API = os.environ.get("NAUSICAA_TMDB_API", "https://api.themoviedb.org/3")
IMG = os.environ.get("NAUSICAA_TMDB_IMG", "https://image.tmdb.org/t/p/w342")
CACHE_FILE = os.path.join(CACHE_DIR, "tmdb.json")
IMAGE_DIR = os.path.join(CACHE_DIR, "tmdb")
TIMEOUT = 10
MAX_IMAGE = 5 * 1024 * 1024
RETRY_NOT_FOUND = 14 * 86400
KEY_PAGE = "https://www.themoviedb.org/settings/api"
LANGS = {"fr": "fr-FR", "de": "de-DE", "en": "en-US"}

_lock = threading.Lock()
_cache: Optional[dict] = None
last_error = ""                  # « key » (clé refusée) ou « network » ; lu puis remis à "" par l'interface


class TmdbError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(CACHE_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save():
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_cache, fh)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass


def forget_cache():
    """Clé changée : « pas trouvé » et refus d'avant ne valent plus."""
    global _cache
    with _lock:
        _cache = {}
        try:
            os.remove(CACHE_FILE)
        except OSError:
            pass


def _request(url: str, key: str) -> bytes:
    key = key.strip()
    headers = {"Accept": "application/json", "User-Agent": "Nausicaa"}
    if key.startswith("eyJ"):                         # jeton de lecture (v4)
        headers["Authorization"] = "Bearer " + key
    elif "api.themoviedb" in url or url.startswith(API):
        url += ("&" if "?" in url else "?") + "api_key=" + urllib.parse.quote(key)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read(MAX_IMAGE + 1)
    except urllib.error.HTTPError as e:
        e.close()
        raise TmdbError("key" if e.code in (401, 403) else "notfound" if e.code == 404 else "network") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TmdbError("network") from e


def _get(path: str, params: dict, key: str) -> dict:
    url = API + path + "?" + urllib.parse.urlencode(params)
    try:
        data = json.loads(_request(url, key).decode("utf-8"))
    except ValueError as e:
        raise TmdbError("network") from e
    return data if isinstance(data, dict) else {}


def _queries(title: str) -> list:
    """« Malcolm : Rien n'a changé » → le titre entier, puis la partie avant « : » / « - »."""
    out = [title]
    for sep in (" : ", ": ", " - ", " – "):
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if head and head not in out:
                out.append(head)
    return out


def _pick(posters: list, lang: str) -> str:
    def score(p):
        return (p.get("vote_average") or 0, p.get("vote_count") or 0)
    for want in (lang, None, "en"):
        found = [p for p in posters if p.get("iso_639_1") == want and p.get("file_path")]
        if found:
            return max(found, key=score)["file_path"]
    found = [p for p in posters if p.get("file_path")]
    return max(found, key=score)["file_path"] if found else ""


def find_poster(kind: str, title: str, year: str, key: str, lang: str = "fr") -> str:
    """Chemin TMDB de l'affiche (« /abc.jpg »), "" si rien trouvé. kind : « tv » ou « movie »."""
    language = LANGS.get(lang, "en-US")
    for q in _queries(title):
        params = {"query": q, "language": language, "include_adult": "false"}
        if year and kind == "movie":
            params["year"] = year
        results = _get(f"/search/{kind}", params, key).get("results") or []
        if not results and year and kind == "movie":
            params.pop("year")
            results = _get(f"/search/{kind}", params, key).get("results") or []
        results = [r for r in results if isinstance(r, dict) and r.get("id")]
        if not results:
            continue
        best = results[0]
        try:
            images = _get(f"/{kind}/{best['id']}/images",
                          {"include_image_language": f"{lang},null,en"}, key).get("posters") or []
        except TmdbError as e:
            if e.code == "key":
                raise
            images = []
        return _pick([p for p in images if isinstance(p, dict)], lang) or best.get("poster_path") or ""
    return ""


def poster(kind: str, title: str, year: str, key: str, lang: str = "fr") -> Optional[str]:
    """Image TMDB téléchargée (fichier local) pour ce titre, ou None. Erreurs notées dans `last_error`."""
    global last_error
    if not key or not title:
        return None
    ident = f"{kind}|{title.casefold()}|{year}|{lang}"
    with _lock:
        known = _load().get(ident)
    now = time.time()
    if isinstance(known, dict):
        path = known.get("path", "")
        if not path and now - known.get("time", 0) < RETRY_NOT_FOUND:
            return None
        if path:
            local = os.path.join(IMAGE_DIR, hashlib.sha1(path.encode()).hexdigest() + os.path.splitext(path)[1])
            if os.path.exists(local):
                return local
    try:
        path = find_poster(kind, title, year, key, lang)
        local = None
        if path:
            local = os.path.join(IMAGE_DIR, hashlib.sha1(path.encode()).hexdigest() + os.path.splitext(path)[1])
            if not os.path.exists(local):
                data = _request(IMG + path, key)
                if len(data) > MAX_IMAGE:
                    raise TmdbError("network")
                os.makedirs(IMAGE_DIR, exist_ok=True)
                tmp = local + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, local)
    except TmdbError as e:
        last_error = e.code
        return None
    with _lock:
        _load()[ident] = {"path": path, "time": int(now)}
        _save()
    return local


def split_year(title: str) -> tuple[str, str]:
    """« Mon Film (2019) » → (« Mon Film », « 2019 »)."""
    m = re.match(r"^(.*?)\s*\(((?:19|20)\d\d)\)$", title)
    return (m.group(1), m.group(2)) if m else (title, "")
