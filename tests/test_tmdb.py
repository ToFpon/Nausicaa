"""Affiches de TMDB contre un faux serveur local (même chemins d'API que themoviedb.org).

    python3.12 -m unittest discover -s tests
    NAUSICAA_NET=1 python3.12 -m unittest tests.test_tmdb      # + vrai TMDB : une fausse clé est refusée
"""

import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="nausicaa-tmdb-")
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(TMP, "cache"))

import gi  # noqa: E402

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

KEY = "0123456789abcdef0123456789abcdef"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.essai"


def png_bytes(color=0xcc3333ff, w=342, h=513) -> bytes:
    pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pix.fill(color)
    return pix.save_to_bufferv("png", [], [])[1]


class Fake(http.server.BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        Fake.calls.append((u.path, q, self.headers.get("Authorization", "")))
        if q.get("api_key") not in (None, KEY) or (q.get("api_key") is None and
                                                  self.headers.get("Authorization") != "Bearer " + TOKEN):
            if not u.path.startswith("/img/"):
                return self.reply(401, {"status_message": "Invalid API key"})
        if u.path == "/3/search/tv":
            if q["query"] == "Malcolm":
                return self.reply(200, {"results": [{"id": 7, "name": "Malcolm", "poster_path": "/en.jpg"}]})
            return self.reply(200, {"results": []})
        if u.path == "/3/search/movie":
            if q["query"] == "Le Grand Bleu" and q.get("year") == "1988":
                return self.reply(200, {"results": [{"id": 9, "title": "Le Grand Bleu", "poster_path": "/gb.jpg"}]})
            return self.reply(200, {"results": []})
        if u.path == "/3/tv/7/images":
            return self.reply(200, {"posters": [
                {"file_path": "/en.jpg", "iso_639_1": "en", "vote_average": 9},
                {"file_path": "/fr1.jpg", "iso_639_1": "fr", "vote_average": 5},
                {"file_path": "/fr2.jpg", "iso_639_1": "fr", "vote_average": 7},
                {"file_path": "/de.jpg", "iso_639_1": "de", "vote_average": 6}]})
        if u.path == "/3/movie/9/images":
            return self.reply(200, {"posters": []})
        if u.path.startswith("/img/"):
            data = png_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.reply(404, {})

    def reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{SERVER.server_address[1]}"
os.environ["NAUSICAA_TMDB_API"] = BASE + "/3"
os.environ["NAUSICAA_TMDB_IMG"] = BASE + "/img"

from nausicaa.backend import posters, tmdb  # noqa: E402

tmdb.API, tmdb.IMG = BASE + "/3", BASE + "/img"


def tearDownModule():
    SERVER.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)


class TmdbTest(unittest.TestCase):
    def setUp(self):
        tmdb.forget_cache()
        Fake.calls.clear()
        tmdb.last_error = ""

    def test_show_poster_in_language(self):
        self.assertEqual(tmdb.find_poster("tv", "Malcolm : Rien n'a changé", "", KEY, "fr"), "/fr2.jpg",
                         "titre entier inconnu → avant le « : » ; affiche française la mieux notée")
        self.assertEqual(tmdb.find_poster("tv", "Malcolm", "", KEY, "de"), "/de.jpg")
        self.assertTrue(all(c[1].get("api_key") == KEY for c in Fake.calls), "clé v3 en paramètre")
        self.assertEqual(Fake.calls[0][1]["language"], "fr-FR")

    def test_token_v4(self):
        self.assertEqual(tmdb.find_poster("tv", "Malcolm", "", TOKEN, "en"), "/en.jpg")
        self.assertTrue(all("api_key" not in c[1] and c[2] == "Bearer " + TOKEN for c in Fake.calls),
                        "jeton v4 dans l'en-tête, jamais dans l'adresse")

    def test_movie_with_year_and_fallback(self):
        self.assertEqual(tmdb.find_poster("movie", "Le Grand Bleu", "1988", KEY, "fr"), "/gb.jpg",
                         "pas d'image par langue : affiche du résultat")
        self.assertEqual(tmdb.split_year("Le Grand Bleu (1988)"), ("Le Grand Bleu", "1988"))
        self.assertEqual(tmdb.split_year("Alien"), ("Alien", ""))

    def test_download_cache_and_not_found(self):
        local = tmdb.poster("tv", "Malcolm", "", KEY, "fr")
        self.assertTrue(local and os.path.getsize(local) > 0)
        n = len(Fake.calls)
        self.assertEqual(tmdb.poster("tv", "Malcolm", "", KEY, "fr"), local)
        self.assertEqual(len(Fake.calls), n, "deuxième fois : rien demandé à TMDB")
        self.assertIsNone(tmdb.poster("tv", "Série inconnue", "", KEY, "fr"))
        n = len(Fake.calls)
        self.assertIsNone(tmdb.poster("tv", "Série inconnue", "", KEY, "fr"))
        self.assertEqual(len(Fake.calls), n, "« pas trouvé » retenu aussi")

    def test_bad_key(self):
        self.assertIsNone(tmdb.poster("tv", "Malcolm", "", "mauvaise", "fr"))
        self.assertEqual(tmdb.last_error, "key")
        tmdb.last_error = ""
        self.assertIsNone(tmdb.poster("tv", "Malcolm", "", "mauvaise", "fr"))
        self.assertEqual(tmdb.last_error, "key", "un refus n'est pas retenu comme « pas trouvé »")

    def test_network_down(self):
        saved = tmdb.API
        tmdb.API = "http://127.0.0.1:9/3"
        try:
            self.assertIsNone(tmdb.poster("tv", "Malcolm", "", KEY, "fr"))
            self.assertEqual(tmdb.last_error, "network")
        finally:
            tmdb.API = saved

    def test_poster_order(self):
        """Image posée à côté > TMDB > image tirée de la vidéo."""
        folder = tempfile.mkdtemp(dir=TMP)
        p = posters.poster_for("show", [folder], "", tmdb_query=("Malcolm", "", KEY, "fr"))
        self.assertEqual(p, posters.cache_path(tmdb.poster("tv", "Malcolm", "", KEY, "fr"), "tmdb"))
        with open(os.path.join(folder, "poster.png"), "wb") as fh:
            fh.write(png_bytes(0x33cc33ff, 600, 900))
        p2 = posters.poster_for("show", [folder], "", tmdb_query=("Malcolm", "", KEY, "fr"))
        self.assertEqual(p2, posters.cache_path(os.path.join(folder, "poster.png"), "image"))


@unittest.skipUnless(os.environ.get("NAUSICAA_NET"), "vrai TMDB : NAUSICAA_NET=1")
class RealTmdbTest(unittest.TestCase):
    def test_fake_key_refused(self):
        saved = tmdb.API, tmdb.IMG
        tmdb.API, tmdb.IMG = "https://api.themoviedb.org/3", "https://image.tmdb.org/t/p/w342"
        try:
            tmdb.forget_cache()
            with self.assertRaises(tmdb.TmdbError) as cm:
                tmdb.find_poster("tv", "Elsbeth", "", KEY, "fr")
            self.assertEqual(cm.exception.code, "key")
        finally:
            tmdb.API, tmdb.IMG = saved


if __name__ == "__main__":
    unittest.main()
