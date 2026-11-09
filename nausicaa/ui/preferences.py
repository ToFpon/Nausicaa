"""Préférences (Adw.PreferencesWindow : vraie fenêtre, le thème du système s'y applique)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import features  # noqa: E402
from ..i18n import LANGUAGE_NAMES, ntr, tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import combo_row  # noqa: E402


def apply_theme():
    Adw.StyleManager.get_default().set_color_scheme({
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(settings["theme"], Adw.ColorScheme.DEFAULT))


def _choice(title: str, key: str, labels: list[str], values: list, subtitle: str = "") -> Adw.ComboRow:
    cur = settings[key]
    row = combo_row(title, labels, values.index(cur) if cur in values else 0, subtitle)
    row.connect("notify::selected", lambda r, *_: settings.__setitem__(key, values[r.get_selected()]))
    return row


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self):
        super().__init__(title=tr("Préférences"), search_enabled=False, default_width=620, default_height=620)
        self.add(self._general())
        self.add(self._features())

    def _general(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Général"), icon_name="preferences-system-symbolic", name="general")
        look = Adw.PreferencesGroup(title=tr("Apparence"))
        theme = _choice(tr("Style"), "theme", [tr("Comme le système"), tr("Clair"), tr("Sombre")],
                        ["system", "light", "dark"])
        theme.connect("notify::selected", lambda *_: apply_theme())
        look.add(theme)
        codes = list(LANGUAGE_NAMES)
        look.add(_choice(tr("Langue"), "language", [tr("Comme le système")] + [LANGUAGE_NAMES[c] for c in codes[1:]],
                         codes, tr("Appliquée au prochain démarrage")))
        page.add(look)
        play = Adw.PreferencesGroup(title=tr("Lecture"))
        play.add(_choice(tr("Volume maximal"), "volume_max", ["100 %", "150 %"], [100, 150],
                         tr("Au-delà de 100 %, le son est amplifié (comme VLC) : utile pour une vidéo trop faible")))
        play.add(_choice(tr("Saut avec ← / →"), "seek_short",
                         [ntr("{0} seconde", "{0} secondes", n).format(n) for n in (5, 10, 30)], [5, 10, 30]))
        play.add(_choice(tr("Cran de molette"), "wheel_step", ["2 %", "5 %", "10 %"], [2, 5, 10],
                         tr("Volume ajouté ou retiré à chaque cran de la molette")))
        page.add(play)
        langs = Adw.PreferencesGroup(title=tr("Langues préférées"),
                                     description=tr("Codes de langue séparés par des virgules, dans l'ordre de "
                                                    "préférence (par exemple « fr,fre,fra,en »). Vide : le choix "
                                                    "du fichier."))
        for key, title in (("audio_language", tr("Piste audio")), ("subtitle_language", tr("Sous-titres"))):
            row = Adw.EntryRow(title=title, text=settings[key], show_apply_button=True)
            row.connect("apply", lambda r, key=key: settings.__setitem__(key, r.get_text().strip()))
            langs.add(row)
        page.add(langs)
        return page

    def _features(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Fonctionnalités"), icon_name="application-x-addon-symbolic",
                                   name="features")
        group = Adw.PreferencesGroup(description=tr("Chaque fonctionnalité s'active ou se coupe tout de "
                                                    "suite, sans redémarrer Nausicaa"))
        self.feature_rows = {}
        for f in features.FEATURES:
            row = Adw.SwitchRow(title=f.title, subtitle=f.description, active=features.enabled(f.id))
            row.add_prefix(Gtk.Image(icon_name=f.icon))
            row.connect("notify::active", lambda r, *_, fid=f.id: features.set_enabled(fid, r.get_active()))
            self.feature_rows[f.id] = row
            group.add(row)
        page.add(group)
        return page
