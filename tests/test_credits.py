"""Générique de fin (backend/credits.py) : chapitres, durée apprise par série."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nausicaa.backend import credits as cr  # noqa: E402

DUR = 45 * 60.0


def ch(*marks):
    return [{"time": t, "title": title} for t, title in marks]


class ChaptersTest(unittest.TestCase):
    def test_titles(self):
        for t in ("Credits", "End Credits", "Ending", "ED", "ED2", "Outro", "Générique", "Generique de fin",
                  "Abspann", "Créditos finales", "Preview", "05 - Credits", "end credits"):
            self.assertTrue(cr.is_credits_title(t), t)
        for t in ("Opening", "OP", "Intro", "Chapter 5", "Credits roll over the city", "Episode", None, 3):
            self.assertFalse(cr.is_credits_title(t), t)

    def test_end_credits_then_next_episode(self):
        found = cr.from_chapters(ch((0, "Intro"), (90, "Episode"), (DUR - 70, "Credits")), DUR)
        self.assertEqual((found.start, found.resume), (DUR - 70, None))

    def test_scene_after_credits_is_kept(self):
        found = cr.from_chapters(ch((0, "Episode"), (DUR - 120, "Credits"), (DUR - 40, "Post-credits scene")), DUR)
        self.assertEqual((found.start, found.resume), (DUR - 120, DUR - 40))
        found = cr.from_chapters(ch((0, "Ep"), (DUR - 120, "Ending"), (DUR - 30, "Preview")), DUR)
        self.assertIsNone(found.resume, "l'aperçu du suivant fait partie de ce qu'on passe")

    def test_opening_credits_ignored(self):
        self.assertIsNone(cr.from_chapters(ch((0, "Credits"), (120, "Episode")), DUR))
        self.assertIsNone(cr.from_chapters(ch((0, "Ep"), (100, "Credits")), 200.0), "clip court")
        self.assertIsNone(cr.from_chapters([{"time": "x"}, "y", None], DUR))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.store = cr.CreditsStore(os.path.join(self.d, "credits.json"))

    def path(self, *parts):
        return os.path.join(self.d, *parts)

    def test_series_key_groups_seasons(self):
        a = cr.series_key(self.path("Séries", "Toto saison 1", "e01.mkv"))
        b = cr.series_key(self.path("Séries", "Toto saison 2", "e01.mkv"))
        c = cr.series_key(self.path("Toto", "Season 03", "e01.mkv"))
        d = cr.series_key(self.path("Toto", "S04", "e01.mkv"))
        self.assertEqual(len({a, b, c, d}), 1)
        self.assertNotEqual(a, cr.series_key(self.path("Titi", "e01.mkv")))
        self.assertIsNone(cr.series_key("https://exemple.fr/flux.m3u8"))

    def test_learn_then_find_for_other_episodes(self):
        e1, e2 = self.path("Toto saison 1", "e01.mkv"), self.path("Toto saison 2", "e05.mkv")
        self.assertEqual(self.store.learn(e1, DUR - 95, DUR), 95.0)
        found = cr.find(e2, DUR + 60, None, cr.CreditsStore(self.store.path))       # relu du fichier
        self.assertEqual((found.start, found.source), (DUR + 60 - 95, "learned"))
        chapters = ch((0, "Ep"), (DUR - 50, "Credits"))
        self.assertEqual(cr.find(e2, DUR, chapters, self.store).start, DUR - 50, "chapitres d'abord")

    def test_learn_refuses_nonsense(self):
        e = self.path("Toto", "e01.mkv")
        self.assertIsNone(self.store.learn(e, 60, DUR), "début de l'épisode")
        self.assertIsNone(self.store.learn(e, DUR - 2, DUR), "trop près de la fin")
        self.assertIsNone(self.store.learn(e, 100, 200), "clip court")
        self.assertEqual(self.store.data, {})

    def test_forget_and_put_back(self):
        e = self.path("Toto", "e01.mkv")
        self.store.learn(e, DUR - 80, DUR)
        self.assertEqual(self.store.forget(e), 80.0)
        self.assertIsNone(self.store.get(e))
        self.store.put_back(e, 80.0)
        self.assertEqual(cr.CreditsStore(self.store.path).get(e), 80.0)

    def test_broken_file(self):
        with open(self.store.path, "w") as fh:
            fh.write('{"toto": "x", "titi": true, "tata": 1e9, "ok": 60}')
        self.assertEqual(cr.CreditsStore(self.store.path).data, {"ok": 60.0})


if __name__ == "__main__":
    unittest.main()
