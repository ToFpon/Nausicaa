"""Fenêtre « Raccourcis clavier » (Gtk.ShortcutsWindow, décrite en XML : add_section n'existe qu'à
partir de GTK 4.14, le XML marche partout)."""

from __future__ import annotations

from xml.sax.saxutils import escape

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from ..i18n import tr  # noqa: E402


def _groups():
    return [
        (tr("Lecture"), [
            ("space", tr("Lire / mettre en pause")),
            ("Right Left", tr("Avancer / reculer de quelques secondes")),
            ("<Control>Right <Control>Left", tr("Avancer / reculer d'une minute")),
            ("period comma", tr("Image suivante / précédente (en pause)")),
            ("n p", tr("Vidéo suivante / précédente")),
        ]),
        (tr("Son et image"), [
            ("Up Down", tr("Monter / baisser le volume")),
            ("m", tr("Couper le son")),
            ("v", tr("Afficher / masquer les sous-titres")),
            ("s", tr("Capture de l'image")),
        ]),
        (tr("Fenêtre"), [
            ("f F11", tr("Plein écran")),
            ("Escape", tr("Quitter le plein écran")),
            ("F9", tr("Afficher la file de lecture")),
            ("<Control>p", tr("Image dans l'image")),
            ("<Control>o", tr("Ouvrir des vidéos")),
            ("<Control>u", tr("Ouvrir une adresse")),
            ("<Control>s", tr("Enregistrer la file")),
            ("<Control>comma", tr("Préférences")),
            ("<Control>q", tr("Quitter")),
        ]),
        (tr("Souris"), [
            (None, tr("Clic : lire / mettre en pause")),
            (None, tr("Double-clic : plein écran")),
            (None, tr("Molette : volume")),
            (None, tr("Clic droit : pistes, sous-titres, vitesse")),
        ]),
    ]


def shortcuts_window() -> Gtk.ShortcutsWindow:
    parts = ['<interface><object class="GtkShortcutsWindow" id="win"><property name="modal">1</property>'
             '<child><object class="GtkShortcutsSection"><property name="section-name">main</property>'
             '<property name="max-height">12</property>']
    for title, rows in _groups():
        parts.append(f'<child><object class="GtkShortcutsGroup"><property name="title">{escape(title)}</property>')
        for accel, text in rows:
            parts.append('<child><object class="GtkShortcutsShortcut">')
            if accel:
                parts.append(f'<property name="accelerator">{escape(accel)}</property>')
            else:
                parts.append('<property name="shortcut-type">gesture</property><property name="icon">'
                             '<object class="GThemedIcon"><property name="name">input-mouse-symbolic</property>'
                             '</object></property>')
            parts.append(f'<property name="title">{escape(text)}</property></object></child>')
        parts.append('</object></child>')
    parts.append('</object></child></object></interface>')
    Gio.ThemedIcon.new("x")             # type GThemedIcon enregistré : le XML le crée par son nom
    builder = Gtk.Builder.new_from_string("".join(parts), -1)
    return builder.get_object("win")
