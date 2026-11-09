"""Affichage par-dessus la vidéo (volume à la molette, avance, vitesse…), comme VLC.

Un seul encart, au milieu en haut de l'image : icône, texte et, pour le volume, une jauge. Il
s'efface tout seul après DELAY ms ; chaque nouvel affichage relance le délai. Couleurs : la classe
« osd » de libadwaita (fond sombre translucide du thème), aucune couleur fixe.
"""

from __future__ import annotations

from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

DELAY = 1300


class Osd(Gtk.Revealer):
    def __init__(self):
        super().__init__(transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=150,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.START, margin_top=24,
                         can_target=False, reveal_child=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("osd")
        box.add_css_class("nausicaa-osd")
        line = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        self.icon = Gtk.Image(pixel_size=24)
        self.label = Gtk.Label()
        self.label.add_css_class("osd-text")
        self.label.add_css_class("numeric")
        line.append(self.icon)
        line.append(self.label)
        box.append(line)
        self.level = Gtk.LevelBar(min_value=0, max_value=1, width_request=220)
        self.level.add_css_class("osd-level")
        # pas de couleurs « bas / haut / plein » (vert / orange…) : une seule teinte, celle du thème
        for name in ("low", "high", "full"):
            self.level.remove_offset_value(name)
        box.append(self.level)
        self.set_child(box)
        self._timer = 0

    def show(self, icon: str, text: str, fraction: Optional[float] = None, over: bool = False):
        """fraction : jauge (0..1, None : pas de jauge) ; over : au-delà de 100 % (amplification)."""
        self.icon.set_from_icon_name(icon)
        self.label.set_label(text)
        self.level.set_visible(fraction is not None)
        if fraction is not None:
            self.level.set_value(max(0.0, min(1.0, fraction)))
        if over:
            self.level.add_css_class("over")
        else:
            self.level.remove_css_class("over")
        self.set_reveal_child(True)
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(DELAY, self._hide)

    def _hide(self):
        self._timer = 0
        self.set_reveal_child(False)
        return False

    def dispose_timer(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
