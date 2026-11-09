"""MPRIS par un vrai client D-Bus (sauté sans bus de session) — ce que font le panneau de GNOME, les
touches multimédia et un casque Bluetooth (BlueZ relaie ses boutons en appels MPRIS) :

    dbus-run-session -- python3.12 -m unittest tests/test_mpris.py
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_backend import run_until  # noqa: E402  (pose aussi des dossiers XDG jetables)

from gi.repository import Gio, GLib  # noqa: E402

import media as testmedia  # noqa: E402

# vidéos à nous : test_backend efface les siennes à la fin de son module
TMP = tempfile.mkdtemp(prefix="nausicaa-mpris-")
LIB = testmedia.library(os.path.join(TMP, "lib"))


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)

from nausicaa.backend import mpris  # noqa: E402
from nausicaa.backend.controller import Controller  # noqa: E402
from nausicaa.backend.player import Player  # noqa: E402
from nausicaa.backend.positions import Positions  # noqa: E402
from nausicaa.settings import settings  # noqa: E402


@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "pas de bus de session")
class MprisTest(unittest.TestCase):
    def setUp(self):
        settings.data.clear()
        self.c = Controller(Player(video=False, audio_output="null"), os.path.join(TMP, "mpris-q.json"),
                            Positions(os.path.join(TMP, "mpris-pos.json")))
        self.raised = []
        self.full = [False]

        def fullscreen(value):
            if value is not None:
                self.full[0] = value
            return self.full[0]
        self.m = mpris.Mpris(self.c, lambda: self.raised.append(1), lambda: None, fullscreen)
        self.m.start()
        self.assertTrue(run_until(lambda: self.m.conn is not None, 5))
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        flags = Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
        self.player = Gio.DBusProxy.new_sync(bus, flags, None, mpris.BUS_NAME, mpris.PATH, mpris.PLAYER, None)
        self.props = Gio.DBusProxy.new_sync(bus, flags, None, mpris.BUS_NAME, mpris.PATH,
                                            "org.freedesktop.DBus.Properties", None)
        self.changes = []
        self.props.connect("g-signal", lambda _p, _s, name, params: self.changes.append(params.unpack()))
        self.c.open_paths([LIB["e2"]], play=False)
        self.assertTrue(run_until(lambda: self.c.player.duration() > 0, 10))

    def tearDown(self):
        self.m.stop()
        self.c.shutdown()

    def call(self, method, args=None):
        self.player.call(method, args, Gio.DBusCallFlags.NONE, 2000, None, None)

    def get(self, prop, iface=mpris.PLAYER):
        out = []
        self.props.call("Get", GLib.Variant("(ss)", (iface, prop)), Gio.DBusCallFlags.NONE, 2000, None,
                        lambda p, res: out.append(p.call_finish(res).unpack()[0]))
        self.assertTrue(run_until(lambda: out, 3))
        return out[0]

    def set(self, prop, value, iface=mpris.PLAYER):
        out = []
        self.props.call("Set", GLib.Variant("(ssv)", (iface, prop, value)), Gio.DBusCallFlags.NONE, 2000,
                        None, lambda p, res: out.append(p.call_finish(res)))
        self.assertTrue(run_until(lambda: out, 3))

    def test_headset_play_pause(self):
        """Double tape du casque = PlayPause ; écouteurs retirés = Pause."""
        self.call("PlayPause")
        self.assertTrue(run_until(lambda: self.c.playing, 5))
        self.assertEqual(self.get("PlaybackStatus"), "Playing")
        md = self.get("Metadata")
        self.assertEqual(md["xesam:title"], "Épisode 2")
        self.assertTrue(md["xesam:url"].startswith("file://"))
        self.assertGreater(md["mpris:length"], 0)
        self.call("Pause")
        self.assertTrue(run_until(lambda: not self.c.playing, 3))
        self.assertEqual(self.get("PlaybackStatus"), "Paused")
        self.call("Pause")                          # deux fois : reste en pause
        self.assertTrue(run_until(lambda: not self.c.playing, 1))
        self.call("Play")
        self.assertTrue(run_until(lambda: self.c.playing, 3))
        self.assertTrue(any(c[0] == mpris.PLAYER and c[1].get("PlaybackStatus") == "Playing" for c in self.changes),
                        "PropertiesChanged annonce la lecture")

    def test_next_previous(self):
        self.call("Next")
        self.assertTrue(run_until(lambda: os.path.basename(self.c.item.path) == "Épisode 10.mp4", 3))
        self.assertTrue(run_until(lambda: any(c[1].get("Metadata", {}).get("xesam:title") == "Épisode 10"
                                              for c in self.changes if c[0] == mpris.PLAYER), 3))
        self.call("Previous")
        self.assertTrue(run_until(lambda: os.path.basename(self.c.item.path) == "Épisode 2.mp4", 3))

    def test_set_properties(self):
        self.set("Volume", GLib.Variant("d", 0.4))
        self.assertTrue(run_until(lambda: settings["volume"] == 40, 3))
        self.set("LoopStatus", GLib.Variant("s", "Playlist"))
        self.assertTrue(run_until(lambda: self.c.queue.repeat == "all", 3))
        self.set("Rate", GLib.Variant("d", 1.5))
        self.assertTrue(run_until(lambda: abs(self.get("Rate") - 1.5) < 0.01, 3))
        self.set("Fullscreen", GLib.Variant("b", True), mpris.ROOT)
        self.assertTrue(run_until(lambda: self.full[0], 3))

    def test_seek_and_raise(self):
        self.call("SetPosition", GLib.Variant("(ox)", (self.m._track_id(), 12_000_000)))
        self.assertTrue(run_until(lambda: abs(self.c.player.position() - 12) < 0.5, 3))
        root = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
                                              mpris.BUS_NAME, mpris.PATH, mpris.ROOT, None)
        root.call("Raise", None, Gio.DBusCallFlags.NONE, 2000, None, None)
        self.assertTrue(run_until(lambda: self.raised, 3))


if __name__ == "__main__":
    unittest.main()
