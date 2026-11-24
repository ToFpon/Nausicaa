"""Bibliothèque de l'accueil : rangement en séries / films, titres, affiches (sans interface).

    python3.12 -m unittest discover -s tests
"""

import locale
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
TMP = tempfile.mkdtemp(prefix="nausicaa-lib-")
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ.setdefault(var, os.path.join(TMP, var.lower()))
locale.setlocale(locale.LC_NUMERIC, "C")

import gi  # noqa: E402

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

import media as testmedia  # noqa: E402
from nausicaa.backend import library, posters  # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def touch(root, rel):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return p


class NamesTest(unittest.TestCase):
    def test_seasons(self):
        for name, want in (("Toto saison 1", ("Toto", 1)), ("Toto Saison 12", ("Toto", 12)),
                           ("The Expanse - Season 02", ("The Expanse", 2)), ("Dark S03", ("Dark", 3)),
                           ("Breaking.Bad.S05", ("Breaking Bad", 5)), ("Toto saison 1 (2010)", ("Toto", 1)),
                           ("Saison 2", ("", 2)), ("S04", ("", 4)), ("Staffel 3", ("", 3)),
                           ("Rocky 2", ("", None)), ("Film (2019)", ("", None)), ("Docs", ("", None))):
            self.assertEqual(library.season_of(name), want, name)

    def test_titles(self):
        for name, want in (("Mon.Film.2019.1080p.BluRay.x264-GRP.mkv", "Mon Film (2019)"),
                           ("Blade Runner 2049 (2017) MULTI 2160p.mkv", "Blade Runner 2049 (2017)"),
                           ("Le Grand Bleu (1988).mkv", "Le Grand Bleu (1988)"), ("2001.mkv", "2001"),
                           ("Alien.mkv", "Alien"), ("le_cinquieme_element.avi", "le cinquieme element")):
            self.assertEqual(library.clean_title(name), want, name)
        self.assertEqual(library.episode_label("Toto S02E05 - Le retour.mkv"), "S02E05")
        self.assertEqual(library.episode_label("Dark 1x02.mkv"), "S01E02")
        self.assertEqual(library.episode_label("Alien.mkv"), "")


class ScanTest(unittest.TestCase):
    def test_tof_layout(self):
        """Rangement de Tof : « Toto saison 1 », « Toto saison 2 »… et les films à la racine."""
        r = tempfile.mkdtemp(dir=TMP)
        for rel in ("Alien.mkv", "Mon.Film.2019.1080p.mkv", "Toto saison 1/Toto 01.mkv", "Toto saison 1/Toto 02.mkv",
                    "Toto saison 1/Toto 10.mkv", "Toto saison 2/Toto S02E01.mkv", "toto Saison 3/x.mkv",
                    "Dark/Saison 1/Dark 1x01.mkv", "Dark/Saison 1/Dark 1x02.mkv", "Dark/Saison 2/e1.mkv",
                    "Film (2019)/Film.2019.mkv", "Docs/Planète.mkv", "Docs/Océans.mkv",
                    "Série/Episode 1.mkv", "Série/Episode 2.mkv", "Série/notes.txt", ".cachée/a.mkv",
                    "Toto saison 1/cover.jpg"):
            touch(r, rel)
        lib = library.scan([r])
        rel = lambda p: os.path.relpath(p, r)  # noqa: E731
        self.assertEqual([rel(f) for f in lib.films],
                         ["Alien.mkv", "Film (2019)/Film.2019.mkv", "Mon.Film.2019.1080p.mkv",
                          "Docs/Océans.mkv", "Docs/Planète.mkv"], "par titre")
        shows = {s.name: s for s in lib.shows}
        self.assertEqual(sorted(shows), ["Dark", "Série", "Toto"])
        toto = shows["Toto"]
        self.assertEqual([s.number for s in toto.seasons], [1, 2, 3], "saisons regroupées, casse ignorée")
        self.assertEqual([os.path.basename(e) for e in toto.seasons[0].episodes],
                         ["Toto 01.mkv", "Toto 02.mkv", "Toto 10.mkv"], "ordre naturel")
        self.assertEqual(len(toto.episodes), 5)
        self.assertEqual(rel(shows["Dark"].folder), "Dark")
        self.assertEqual([s.number for s in shows["Dark"].seasons], [1, 2])
        self.assertEqual(shows["Série"].seasons[0].number, None)

    def test_punctuation_ignored(self):
        """« Hunting : Chasseurs » et « Hunting - Chasseurs » (vu chez Tof) : une seule série."""
        r = tempfile.mkdtemp(dir=TMP)
        touch(r, "Hunting - Chasseurs de criminels saison 1/e1.mkv")
        touch(r, "Hunting _ Chasseurs de criminels saison 2/e1.mkv")
        touch(r, "Hunting  Chasseurs de criminels Saison 3/e1.mkv")
        lib = library.scan([r])
        self.assertEqual([[x.number for x in s.seasons] for s in lib.shows], [[1, 2, 3]])

    def test_several_roots_and_missing(self):
        a, b = tempfile.mkdtemp(dir=TMP), tempfile.mkdtemp(dir=TMP)
        touch(a, "Toto saison 1/e1.mkv")
        touch(b, "Toto saison 2/e1.mkv")
        lib = library.scan([a, b, os.path.join(TMP, "absent")])
        self.assertEqual([(s.name, [x.number for x in s.seasons]) for s in lib.shows], [("Toto", [1, 2])])
        self.assertTrue(library.scan([os.path.join(TMP, "absent")]).empty)

    def test_symlink_loop(self):
        r = tempfile.mkdtemp(dir=TMP)
        touch(r, "Toto saison 1/e1.mkv")
        os.symlink(r, os.path.join(r, "Toto saison 1", "boucle"))
        lib = library.scan([r])
        self.assertEqual(len(lib.shows), 1)


class LooseEpisodesTest(unittest.TestCase):
    def test_episodes_outside_series_folders(self):
        """Épisodes « Série S01E02 » posés à la racine ou seuls dans un dossier : des séries, plus des films (vu chez
        Tof : accueil sans séries). Même saison trouvée à deux endroits : réunie."""
        r = tempfile.mkdtemp(dir=TMP)
        for rel in ("Breaking.Bad.S01E02.720p.mkv", "Breaking.Bad.S01E01.720p.mkv", "Breaking Bad S02E01.mkv",
                    "Alien.mkv", "Blade Runner 2049 (2017).mkv",
                    "Dark/S01E03.mkv",                                   # seul dans son dossier, sans nom
                    "Toto saison 1/Toto 01.mkv", "Toto saison 1/Toto 02.mkv", "Toto.S01E09.mkv",
                    "Un film/Le Film.mkv"):
            touch(r, rel)
        lib = library.scan([r])
        rel = lambda p: os.path.relpath(p, r)  # noqa: E731
        self.assertEqual(sorted(rel(f) for f in lib.films),
                         ["Alien.mkv", "Blade Runner 2049 (2017).mkv", "Un film/Le Film.mkv"])
        shows = {s.name: s for s in lib.shows}
        self.assertEqual(sorted(shows), ["Breaking Bad", "Dark", "Toto"])
        bb = shows["Breaking Bad"]
        self.assertEqual([x.number for x in bb.seasons], [1, 2])
        self.assertEqual([rel(e) for e in bb.seasons[0].episodes],
                         ["Breaking.Bad.S01E01.720p.mkv", "Breaking.Bad.S01E02.720p.mkv"])
        toto = shows["Toto"]
        self.assertEqual(len(toto.seasons), 1, "saison 1 réunie : dossier + épisode posé à côté")
        self.assertEqual([os.path.basename(e) for e in toto.episodes], ["Toto 01.mkv", "Toto 02.mkv", "Toto.S01E09.mkv"])


class PosterTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=TMP)

    def image(self, path, w=400, h=300):
        pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
        pix.fill(0x3366ccff)
        pix.savev(path, "png", [], [])
        return path

    def size(self, path):
        pix = GdkPixbuf.Pixbuf.new_from_file(path)
        return pix.get_width(), pix.get_height()

    def test_local_images(self):
        season = os.path.join(self.dir, "Toto saison 1")
        os.makedirs(season)
        self.assertIsNone(posters.local_image("show", [season]))
        self.image(os.path.join(season, "Folder.PNG"))
        self.assertEqual(posters.local_image("show", [season]), os.path.join(season, "Folder.PNG"))
        film = touch(self.dir, "Alien.mkv")
        self.assertIsNone(posters.local_image("film", film), "« folder » à la racine ne vaut pas pour un film")
        self.image(os.path.join(self.dir, "alien-poster.jpg"))
        self.assertEqual(posters.local_image("film", film), os.path.join(self.dir, "alien-poster.jpg"))
        alone = touch(self.dir, "Film (2019)/Film.mkv")
        self.image(os.path.join(self.dir, "Film (2019)", "poster.jpg"))
        self.assertEqual(posters.local_image("film", alone, alone=True),
                         os.path.join(self.dir, "Film (2019)", "poster.jpg"))
        p = posters.poster_for("show", [season], "")
        self.assertEqual(self.size(p), (posters.WIDTH, posters.HEIGHT), "recadrée en 2:3")

    def test_frame_from_video(self):
        v = testmedia.video(os.path.join(self.dir, "ep.mkv"), 4)
        t = time.time()
        p = posters.poster_for("film", v, v)
        self.assertIsNotNone(p, "image tirée de la vidéo par la libmpv")
        self.assertEqual(self.size(p), (posters.WIDTH, posters.HEIGHT))
        self.assertLess(time.time() - t, 10)
        self.assertEqual(posters.poster_for("film", v, v), p, "cache")
        bad = os.path.join(self.dir, "faux.mkv")
        with open(bad, "w") as fh:
            fh.write("pas une vidéo")
        self.assertIsNone(posters.poster_for("film", bad, bad))

    def test_chosen_image_wins(self):
        v = testmedia.video(os.path.join(self.dir, "ep.mkv"), 2)
        chosen = self.image(os.path.join(self.dir, "mon affiche.png"), 600, 900)
        p = posters.poster_for("film", v, v, chosen=chosen)
        self.assertEqual(p, posters.cache_path(chosen, "image"))

    def test_worker(self):
        loop = GLib.MainLoop()
        got = []
        w = posters.PosterWorker()
        w.request("a", lambda: "A", lambda r: got.append(r))
        w.request("b", lambda: 1 / 0, lambda r: (got.append(r), loop.quit()))
        GLib.timeout_add(5000, loop.quit)
        loop.run()
        self.assertEqual(sorted(got, key=str), ["A", None], "une erreur ne bloque pas les autres")


if __name__ == "__main__":
    unittest.main()
