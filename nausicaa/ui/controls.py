"""Barre de commandes du bas : lecture, avance, temps, volume, pistes, file, plein écran."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..format import duration as fmt  # noqa: E402
from ..i18n import tr  # noqa: E402
from ..settings import settings  # noqa: E402


def volume_icon(volume: float, muted: bool) -> str:
    if muted or volume <= 0:
        return "audio-volume-muted-symbolic"
    if volume < 34:
        return "audio-volume-low-symbolic"
    if volume < 67:
        return "audio-volume-medium-symbolic"
    return "audio-volume-high-symbolic"


def _button(icon: str, tooltip: str, action: str = "") -> Gtk.Button:
    b = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    b.add_css_class("flat")
    if action:
        b.set_action_name(action)
    return b


class Controls(Gtk.Box):
    def __init__(self, controller):
        super().__init__(spacing=4)
        self.add_css_class("toolbar")
        self.add_css_class("nausicaa-controls")
        self.controller = controller
        self.prev = _button("media-skip-backward-symbolic", tr("Précédent"), "win.previous")
        self.play = _button("media-playback-start-symbolic", tr("Lire"), "win.toggle")
        self.play.add_css_class("circular")
        self.next = _button("media-skip-forward-symbolic", tr("Suivant"), "win.next")
        for b in (self.prev, self.play, self.next):
            self.append(b)

        self.elapsed = Gtk.Label(label="0:00", width_chars=7, xalign=1)
        self.elapsed.add_css_class("numeric")
        self.elapsed.add_css_class("dim-label")
        self.append(self.elapsed)
        self.scale = Gtk.Scale(hexpand=True, draw_value=False, has_tooltip=True,
                               adjustment=Gtk.Adjustment(lower=0, upper=1, step_increment=5, page_increment=60))
        self.scale.add_css_class("seek")
        self.scale.set_can_focus(False)           # ← / → restent à la vidéo (raccourcis d'avance)
        self._seek_handler = self.scale.connect("change-value", self._on_change_value)
        self.scale.connect("query-tooltip", self._on_tooltip)
        self.append(self.scale)
        self.total = Gtk.Button(label="0:00", valign=Gtk.Align.CENTER,
                                tooltip_text=tr("Durée totale ou temps restant"))
        self.total.add_css_class("flat")
        self.total.add_css_class("numeric")
        self.total.add_css_class("time-button")
        self.total.connect("clicked", self._toggle_remaining)
        self.append(self.total)
        self._remaining = bool(settings["show_remaining"])     # choix retenu d'une séance à l'autre
        self._duration = 0.0

        # volume : bouton + petite fenêtre avec une réglette verticale, molette sur le bouton
        self.volume = Gtk.MenuButton(icon_name="audio-volume-high-symbolic", tooltip_text=tr("Volume"),
                                     valign=Gtk.Align.CENTER)
        self.volume.add_css_class("flat")
        pop = Gtk.Popover()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.volume_scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, inverted=True, height_request=160,
                                      adjustment=Gtk.Adjustment(lower=0, upper=100, step_increment=5,
                                                                page_increment=10))
        self.volume_scale.set_draw_value(True)
        self.volume_scale.set_value_pos(Gtk.PositionType.BOTTOM)
        self.volume_scale.set_format_value_func(lambda _s, v: f"{int(round(v))} %")
        self._volume_handler = self.volume_scale.connect(
            "value-changed", lambda s: controller.set_volume(s.get_value()))
        vbox.append(self.volume_scale)
        self.mute = Gtk.ToggleButton(icon_name="audio-volume-muted-symbolic", tooltip_text=tr("Couper le son"),
                                     halign=Gtk.Align.CENTER)
        self.mute.add_css_class("flat")
        self._mute_handler = self.mute.connect("toggled", lambda b: controller.set_muted(b.get_active()))
        vbox.append(self.mute)
        pop.set_child(vbox)
        self.volume.set_popover(pop)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self._on_volume_wheel)
        self.volume.add_controller(wheel)
        self._wheel_acc = 0.0
        self.append(self.volume)

        self.tracks = Gtk.MenuButton(icon_name="media-view-subtitles-symbolic",
                                     tooltip_text=tr("Pistes audio et sous-titres"), valign=Gtk.Align.CENTER)
        self.tracks.add_css_class("flat")
        self.append(self.tracks)
        self.playlist = Gtk.ToggleButton(icon_name="view-list-symbolic", tooltip_text=tr("File de lecture"),
                                         valign=Gtk.Align.CENTER, action_name="win.playlist")
        self.playlist.add_css_class("flat")
        self.append(self.playlist)
        self.fullscreen = _button("view-fullscreen-symbolic", tr("Plein écran"), "win.fullscreen")
        self.append(self.fullscreen)
        self.dragging = False
        press = Gtk.GestureClick()
        press.connect("pressed", lambda *_: setattr(self, "dragging", True))
        press.connect("stopped", lambda *_: setattr(self, "dragging", False))
        press.connect("released", lambda *_: setattr(self, "dragging", False))
        self.scale.add_controller(press)
        self.sync_volume()

    # -- avance -------------------------------------------------------------------------------
    def _on_change_value(self, _scale, _scroll, value):
        if self._duration > 0:
            self.controller.seek_to(max(0.0, min(value, self._duration)))
        return False

    def _on_tooltip(self, scale, x, _y, _kb, tooltip):
        if self._duration <= 0:
            return False
        width = scale.get_width()
        if width <= 0:
            return False
        tooltip.set_text(fmt(max(0.0, min(1.0, x / width)) * self._duration))
        return True

    def set_duration(self, seconds: float):
        self._duration = seconds
        self.scale.set_sensitive(seconds > 0)
        self.scale.get_adjustment().set_upper(max(seconds, 1))
        self._update_total(self.scale.get_value())

    def set_position(self, seconds: float):
        if not self.dragging:
            self.scale.handler_block(self._seek_handler)
            self.scale.set_value(seconds)
            self.scale.handler_unblock(self._seek_handler)
        self.elapsed.set_label(fmt(seconds))
        if self._remaining:
            self._update_total(seconds)

    def _toggle_remaining(self, *_):
        self._remaining = not self._remaining
        settings["show_remaining"] = self._remaining
        self._update_total(self.scale.get_value())

    def _update_total(self, position: float):
        if self._duration <= 0:
            self.total.set_label("—" if self.controller.item and self.controller.item.stream else "0:00")
        elif self._remaining:
            self.total.set_label("-" + fmt(self._duration - position))
        else:
            self.total.set_label(fmt(self._duration))

    # -- lecture ------------------------------------------------------------------------------
    def set_playing(self, playing: bool):
        self.play.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play.set_tooltip_text(tr("Pause") if playing else tr("Lire"))

    # -- volume -------------------------------------------------------------------------------
    def sync_volume(self):
        vol, muted = settings["volume"], settings["muted"]
        self.volume.set_icon_name(volume_icon(vol, muted))
        self.volume_scale.handler_block(self._volume_handler)
        self.volume_scale.get_adjustment().set_upper(settings["volume_max"])
        self.volume_scale.set_value(vol)
        self.volume_scale.handler_unblock(self._volume_handler)
        self.mute.handler_block(self._mute_handler)
        self.mute.set_active(muted)
        self.mute.handler_unblock(self._mute_handler)

    def _on_volume_wheel(self, _ctl, _dx, dy):
        self._wheel_acc -= dy
        while abs(self._wheel_acc) >= 1:
            step = 1 if self._wheel_acc > 0 else -1
            self._wheel_acc -= step
            self.controller.change_volume(step * settings["wheel_step"])
        return True
