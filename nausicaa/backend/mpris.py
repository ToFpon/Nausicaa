"""MPRIS 2 (org.mpris.MediaPlayer2) : contrôle depuis le panneau de GNOME / Zorin, l'écran verrouillé,
les touches multimédia du clavier et les CASQUES BLUETOOTH (BlueZ relaie l'AVRCP du casque vers le
lecteur MPRIS actif : double tape = lecture / pause, écouteurs retirés = Pause). Repris d'Ithaca.

Nom de bus : org.mpris.MediaPlayer2.nausicaa. Propriétés par les fonctions get / set de
register_object ; changements annoncés par PropertiesChanged, sauts par Seeked.
"""

from __future__ import annotations

import hashlib
from typing import Callable, Optional

from gi.repository import Gio, GLib

from .. import APP_ID, APP_NAME
from ..settings import settings

BUS_NAME = "org.mpris.MediaPlayer2.nausicaa"
PATH = "/org/mpris/MediaPlayer2"
ROOT = "org.mpris.MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

XML = """<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/><method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="CanSetFullscreen" type="b" access="read"/>
    <property name="Fullscreen" type="b" access="readwrite"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/><method name="Previous"/><method name="Pause"/><method name="PlayPause"/>
    <method name="Stop"/><method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition"><arg direction="in" name="TrackId" type="o"/><arg direction="in" name="Position" type="x"/></method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>"""

LOOP = {"consecutive": "None", "all": "Playlist", "one": "Track"}
MIME = ["video/mp4", "video/x-matroska", "video/webm", "video/x-msvideo", "video/quicktime", "video/mpeg",
        "video/mp2t", "video/ogg", "video/x-flv", "video/x-ms-wmv", "audio/mpeg", "audio/ogg", "audio/flac"]


class Mpris:
    """controller : backend.controller.Controller ; raise_cb / quit_cb : fenêtre et application ;
    fullscreen_cb(valeur ou None) : lire / changer le plein écran."""

    def __init__(self, controller, raise_cb: Callable[[], None], quit_cb: Callable[[], None],
                 fullscreen_cb: Optional[Callable] = None):
        self.c = controller
        self.raise_cb = raise_cb
        self.quit_cb = quit_cb
        self.fullscreen_cb = fullscreen_cb
        self.conn: Optional[Gio.DBusConnection] = None
        self._owner = 0
        self._ids: list[int] = []
        self._last: dict = {}
        self.node = Gio.DBusNodeInfo.new_for_xml(XML)

    # -- bus ---------------------------------------------------------------------------------
    def start(self):
        if self._owner:
            return
        self._owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                                       self._on_bus, None, None)
        self.c.connect(self._on_event)

    def stop(self):
        self.c.disconnect(self._on_event)
        if self.conn is not None:
            for i in self._ids:
                self.conn.unregister_object(i)
        self._ids = []
        if self._owner:
            Gio.bus_unown_name(self._owner)
            self._owner = 0
        self.conn = None

    def _on_bus(self, conn, _name):
        self.conn = conn
        for iface in self.node.interfaces:
            self._ids.append(conn.register_object(PATH, iface, self._on_call, self._on_get, self._on_set))
        self._last = self._player_props()

    # -- propriétés ----------------------------------------------------------------------------
    def _root_props(self) -> dict:
        full = bool(self.fullscreen_cb(None)) if self.fullscreen_cb else False
        return {"CanQuit": GLib.Variant("b", True), "CanRaise": GLib.Variant("b", True),
                "CanSetFullscreen": GLib.Variant("b", self.fullscreen_cb is not None),
                "Fullscreen": GLib.Variant("b", full),
                "HasTrackList": GLib.Variant("b", False), "Identity": GLib.Variant("s", APP_NAME),
                "DesktopEntry": GLib.Variant("s", APP_ID),
                "SupportedUriSchemes": GLib.Variant("as", ["file", "http", "https"]),
                "SupportedMimeTypes": GLib.Variant("as", MIME)}

    def _track_id(self) -> str:
        item = self.c.item
        if item is None:
            return NO_TRACK
        digest = hashlib.sha1(item.path.encode("utf-8", "surrogateescape")).hexdigest()[:16]
        return "/io/github/nausicaa/Nausicaa/Track/" + digest

    def metadata(self) -> dict:
        item = self.c.item
        if item is None or self.c.player.path is None:
            return {"mpris:trackid": GLib.Variant("o", NO_TRACK)}
        url = item.path if item.stream else Gio.File.new_for_path(item.path).get_uri()
        title = item.title or (self.c.player.get("media-title") if item.stream else "") or item.display_title
        md = {"mpris:trackid": GLib.Variant("o", self._track_id()),
              "xesam:title": GLib.Variant("s", _text(title)),
              "xesam:url": GLib.Variant("s", _text(url))}
        duration = self.c.player.duration()
        if duration > 0:
            md["mpris:length"] = GLib.Variant("x", int(duration * 1_000_000))
        return md

    def _status(self) -> str:
        if self.c.playing:
            return "Playing"
        return "Paused" if self.c.player.path is not None else "Stopped"

    def _player_props(self) -> dict:
        has = len(self.c.queue) > 0
        q = self.c.queue
        return {"PlaybackStatus": GLib.Variant("s", self._status()),
                "LoopStatus": GLib.Variant("s", LOOP.get(q.repeat, "None")),
                "Rate": GLib.Variant("d", float(self.c.player.get("speed", 1.0) or 1.0)),
                "MinimumRate": GLib.Variant("d", 0.25), "MaximumRate": GLib.Variant("d", 4.0),
                "Shuffle": GLib.Variant("b", q.shuffled),
                "Metadata": GLib.Variant("a{sv}", self.metadata()),
                # 0..1 = 0..100 % (au-delà : amplification, 1,5 au plus)
                "Volume": GLib.Variant("d", 0.0 if settings["muted"] else settings["volume"] / 100.0),
                "CanGoNext": GLib.Variant("b", has and q.next_index() is not None and len(q) > 1),
                "CanGoPrevious": GLib.Variant("b", has),
                "CanPlay": GLib.Variant("b", has), "CanPause": GLib.Variant("b", has),
                "CanSeek": GLib.Variant("b", self.c.player.path is not None and self.c.player.duration() > 0),
                "CanControl": GLib.Variant("b", True)}

    def _on_get(self, _conn, _sender, _path, iface, prop):
        if iface == ROOT:
            return self._root_props().get(prop)
        if prop == "Position":
            return GLib.Variant("x", int(self.c.player.position() * 1_000_000))
        return self._player_props().get(prop)

    def _on_set(self, _conn, _sender, _path, _iface, prop, value):
        v = value.unpack()
        if prop == "Volume":
            self.c.set_volume(max(0.0, float(v)) * 100.0)
        elif prop == "Shuffle":
            self.c.set_shuffled(bool(v))
        elif prop == "LoopStatus":
            mode = {"None": "consecutive", "Playlist": "all", "Track": "one"}.get(v)
            if mode:
                self.c.queue.set_repeat(mode)
                settings["repeat"] = mode
        elif prop == "Rate":
            if float(v) > 0:
                self.c.player.set_property("speed", max(0.25, min(4.0, float(v))))
        elif prop == "Fullscreen" and self.fullscreen_cb:
            self.fullscreen_cb(bool(v))
        return True

    # -- méthodes ------------------------------------------------------------------------------
    def _on_call(self, _conn, _sender, _path, iface, method, params, inv):
        args = params.unpack()
        if iface == ROOT:
            GLib.idle_add(lambda: ((self.raise_cb if method == "Raise" else self.quit_cb)(), False)[1])
        elif method == "PlayPause":
            self.c.toggle()
        elif method == "Play":
            self.c.set_paused(False)
        elif method in ("Pause", "Stop"):           # Stop : pause (on garde la vidéo et sa position)
            if self.c.player.path is not None:
                self.c.set_paused(True)
        elif method == "Next":
            self.c.next()
        elif method == "Previous":
            self.c.previous()
        elif method == "Seek":
            pos = self.c.player.position() + args[0] / 1_000_000
            duration = self.c.player.duration()
            if duration and pos > duration:
                self.c.next()
            else:
                self.c.seek_to(max(0.0, pos))
                self._seeked(max(0.0, pos))
        elif method == "SetPosition":
            if self.c.item is not None and args[0] == self._track_id():
                self.c.seek_to(args[1] / 1_000_000)
                self._seeked(args[1] / 1_000_000)
        elif method == "OpenUri":
            f = Gio.File.new_for_uri(args[0])
            self.c.open_paths([f.get_path() or args[0]])
        inv.return_value(None)

    # -- annonces --------------------------------------------------------------------------------
    def _seeked(self, position: float):
        if self.conn is not None:
            self.conn.emit_signal(None, PATH, PLAYER, "Seeked", GLib.Variant("(x)", (int(position * 1_000_000),)))

    def _on_event(self, event, *args):
        if self.conn is None or event == "position":
            return
        props = self._player_props()
        changed = {k: v for k, v in props.items() if self._last.get(k) != v}
        self._last = props
        if changed:
            self.conn.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                  GLib.Variant("(sa{sv}as)", (PLAYER, changed, [])))

    def fullscreen_changed(self):
        if self.conn is not None and self.fullscreen_cb:
            self.conn.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                  GLib.Variant("(sa{sv}as)", (ROOT, {"Fullscreen": GLib.Variant(
                                      "b", bool(self.fullscreen_cb(None)))}, [])))


def _text(s: str) -> str:
    """Nom non UTF-8 (surrogateescape) : D-Bus n'accepte que de l'UTF-8 valide."""
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
