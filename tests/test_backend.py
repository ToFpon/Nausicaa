"""Moteur sans interface : médias, file, positions de reprise, contrôleur sur un vrai mpv (sortie
vidéo et son factices).

    python3.12 -m unittest discover -s tests
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
TMP = tempfile.mkdtemp(prefix="nausicaa-test-")
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(TMP, var.lower())

from gi.repository import GLib  # noqa: E402

import media as testmedia  # noqa: E402
from nausicaa import features  # noqa: E402
from nausicaa.backend import media, playlists, positions  # noqa: E402
from nausicaa.backend.controller import Controller  # noqa: E402
from nausicaa.backend.media import Item  # noqa: E402
from nausicaa.backend.player import Player  # noqa: E402
from nausicaa.backend.playqueue import PlayQueue  # noqa: E402
from nausicaa.settings import Settings, settings  # noqa: E402

LIB = testmedia.library(os.path.join(TMP, "lib"))


def run_until(cond, timeout=10.0):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        while ctx.pending():
            ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class MediaTest(unittest.TestCase):
    def test_natural_order_and_siblings(self):
        found = media.siblings(LIB["e2"])
        self.assertEqual([os.path.basename(p) for p in found], ["Épisode 1.mp4", "Épisode 2.mp4", "Épisode 10.mp4"])

    def test_expand_folder(self):
        found = media.expand([LIB["root"]])
        names = [os.path.basename(p) for p in found]
        self.assertIn("chanson.mp3", names)
        self.assertNotIn("notes.txt", names)
        self.assertNotIn("Épisode 2.fr.srt", names)
        self.assertLess(names.index("Épisode 2.mp4"), names.index("Épisode 10.mp4"))

    def test_expand_playlist_and_no_duplicates(self):
        pl = os.path.join(TMP, "liste.m3u")
        playlists.save([Item(LIB["e10"], "Dixième"), Item(LIB["e1"])], pl)
        titles = {}
        found = media.expand([pl, LIB["e1"]], titles)
        self.assertEqual(found, [LIB["e10"], LIB["e1"]])
        self.assertEqual(titles[LIB["e10"]], "Dixième")
        with open(pl, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("\nlib/Séries/La Mer/Épisode 10.mp4\n", text)      # chemin relatif à la liste

    def test_external_subtitles(self):
        self.assertEqual([os.path.basename(p) for p in media.external_subtitles(LIB["e2"])], ["Épisode 2.fr.srt"])

    def test_name_key_bytes(self):
        self.assertIsInstance(media.name_key("⽨ é"), bytes)


class PositionsTest(unittest.TestCase):
    def test_rules(self):
        pos = positions.Positions(os.path.join(TMP, "pos.json"))
        film = LIB["film"]
        self.assertFalse(pos.remember(film, 5, 90))            # tout début : rien
        self.assertEqual(pos.get(film), 0)
        self.assertTrue(pos.remember(film, 40, 90))
        self.assertEqual(pos.get(film), 40)
        pos.save()
        self.assertEqual(positions.Positions(os.path.join(TMP, "pos.json")).get(film), 40)
        self.assertTrue(pos.remember(film, 85, 90))            # fin : vue, oubliée
        self.assertEqual(pos.get(film), 0)
        self.assertFalse(pos.remember(LIB["e1"], 2, 3))        # clip court
        self.assertFalse(pos.remember("https://exemple/flux", 100, 1000))

    def test_damaged_file(self):
        path = os.path.join(TMP, "abime.json")
        with open(path, "w") as fh:
            fh.write('{"x": 3, "y": {"pos": "a"}}')
        self.assertEqual(positions.Positions(path).data, {})


class SettingsTest(unittest.TestCase):
    def test_wrong_types_ignored(self):
        path = os.path.join(TMP, "settings.json")
        with open(path, "w") as fh:
            fh.write('{"volume": "fort", "muted": 1, "volume_max": 150, "inconnu": 3}')
        s = Settings(path)
        self.assertEqual(s["volume"], 100)
        self.assertFalse(s["muted"])
        self.assertEqual(s["volume_max"], 150)


class QueueTest(unittest.TestCase):
    def test_add_once_and_remove_current(self):
        q = PlayQueue()
        a, b, c = Item("/a"), Item("/b"), Item("/c")
        q.add([a, b, c, Item("/a")])
        self.assertEqual(len(q), 3)
        q.set_current(1)
        q.remove([b])
        self.assertIs(q.current, c)


class PlayerTest(unittest.TestCase):
    def setUp(self):
        self.player = Player(video=False, audio_output="null")

    def tearDown(self):
        self.player.shutdown()

    def test_start_position_and_eof(self):
        self.player.load(LIB["e1"], start=2.0)
        self.assertTrue(run_until(lambda: self.player.position() >= 2.0))
        self.assertTrue(run_until(lambda: self.player.get("eof-reached")))
        self.assertEqual(len(self.player.tracks("video")), 1)

    def test_two_audio_tracks(self):
        self.player.load(LIB["e2"])
        self.assertTrue(run_until(lambda: len(self.player.tracks("audio")) == 2))
        langs = [t.get("lang") for t in self.player.tracks("audio")]
        self.assertEqual(langs, ["fre", "eng"])
        self.assertTrue(run_until(lambda: len(self.player.tracks("sub")) == 1))   # Épisode 2.fr.srt


class ControllerTest(unittest.TestCase):
    def setUp(self):
        settings.data.clear()
        self.events = []
        self.pos = positions.Positions(os.path.join(TMP, f"pos-{id(self)}.json"))
        self.c = Controller(Player(video=False, audio_output="null"), os.path.join(TMP, f"q-{id(self)}.json"),
                            self.pos)
        self.c.connect(lambda e, *a: self.events.append((e, a)))

    def tearDown(self):
        self.c.shutdown()

    def test_next_episodes_then_chain(self):
        self.c.open_paths([LIB["e2"]])
        self.assertTrue(run_until(lambda: self.c.item is not None))
        self.assertEqual([os.path.basename(i.path) for i in self.c.queue.items],
                         ["Épisode 1.mp4", "Épisode 2.mp4", "Épisode 10.mp4"])
        self.assertEqual(os.path.basename(self.c.item.path), "Épisode 2.mp4")
        self.assertTrue(run_until(lambda: self.c.player.duration() > 0))
        self.c.seek_to(self.c.player.duration() - 1)            # 30 s : on saute à la fin
        self.assertTrue(run_until(lambda: os.path.basename(self.c.player.path or "") == "Épisode 10.mp4", 12))
        self.assertTrue(run_until(lambda: ("ended", ()) in self.events, 12))

    def test_next_episodes_off(self):
        features.set_enabled("next_episodes", False)
        try:
            self.c.open_paths([LIB["e2"]])
            self.assertTrue(run_until(lambda: self.c.item is not None))
            self.assertEqual(len(self.c.queue), 1)
        finally:
            features.set_enabled("next_episodes", True)

    def test_unreadable_is_skipped(self):
        self.c.open_paths([LIB["fake"], LIB["e1"]])
        self.assertTrue(run_until(lambda: any(e == "error" for e, _ in self.events), 10))
        self.assertTrue(run_until(lambda: self.c.player.path == LIB["e1"], 10))

    def test_resume(self):
        self.c.open_paths([LIB["film"]])
        self.assertTrue(run_until(lambda: self.c.player.duration() > 0))
        self.c.seek_to(40)
        self.assertTrue(run_until(lambda: self.c.player.position() >= 40))
        self.c.stop()
        self.assertGreaterEqual(self.pos.get(LIB["film"]), 40)
        self.events.clear()
        self.c.play_item(self.c.queue.items[0])
        self.assertTrue(run_until(lambda: self.c.player.position() >= 40))
        self.assertTrue(any(e == "resumed" for e, _ in self.events))
        self.c.restart()
        self.assertTrue(run_until(lambda: self.c.player.position() < 10))
        self.assertEqual(self.pos.get(LIB["film"]), 0)

    def test_volume_limits(self):
        self.c.set_volume(140)
        self.assertEqual(settings["volume"], 100)               # sans amplification : 100 au plus
        settings["volume_max"] = 150
        self.c.set_volume(140)
        self.assertEqual(settings["volume"], 140)
        settings["volume_max"] = 100
        self.assertEqual(settings["volume"], 100)               # amplification coupée : retour à 100
        self.c.set_volume(-5)
        self.assertEqual(settings["volume"], 0)

    def test_queue_saved_and_restored(self):
        self.c.open_paths([LIB["serie"]])
        self.assertTrue(run_until(lambda: len(self.c.queue) == 3))
        self.c.play_index(2)
        self.c.save()
        data = Controller.saved_queue(self.c.queue_file)
        self.assertEqual(data["current"], LIB["e10"])
        self.c.clear()
        self.assertTrue(self.c.restore_saved(play=False))
        self.assertEqual(self.c.item.path, LIB["e10"])


if __name__ == "__main__":
    unittest.main()
