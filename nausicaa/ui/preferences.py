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


def default_app_row(title: str, what: str, app_name: str) -> Adw.SwitchRow:
    """Interrupteur « application par défaut » (defaultapp.py), comme celui d'Odysseus : l'état affiché est
    celui du système ; grisé quand l'application tourne depuis les sources (pas de lanceur installé)."""
    from .. import defaultapp
    row = Adw.SwitchRow(title=title, active=defaultapp.is_default())
    row.set_subtitle(what + "\n" + defaultapp.COMMAND)
    row.set_subtitle_lines(0)
    if not defaultapp.installed():
        row.set_subtitle(row.get_subtitle() + "\n" + tr("{0} n'est pas installé (lancé depuis les sources)").format(app_name))
        row.set_sensitive(False)
        return row

    def toggled(r, *_):
        if r.get_active() != defaultapp.is_default() and not defaultapp.set_default(r.get_active()):
            r.set_active(defaultapp.is_default())
    row.connect("notify::active", toggled)
    return row


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self):
        super().__init__(title=tr("Préférences"), search_enabled=False, default_width=620, default_height=620)
        self.add(self._general())
        self.add(self._features())

    def _general(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=tr("Général"), icon_name="preferences-system-symbolic", name="general")
        system = Adw.PreferencesGroup()
        system.add(default_app_row(tr("Lecteur vidéo par défaut"),
                                    tr("Les vidéos s'ouvrent avec Nausicaa (la musique reste à Ithaca) ; coupé, l'application d'avant reprend la main."),
                                    "Nausicaa"))
        page.add(system)
        look = Adw.PreferencesGroup(title=tr("Apparence"))
        theme = _choice(tr("Style"), "theme", [tr("Comme le système"), tr("Clair"), tr("Sombre")],
                        ["system", "light", "dark"])
        theme.connect("notify::selected", lambda *_: apply_theme())
        look.add(theme)
        codes = list(LANGUAGE_NAMES)
        look.add(_choice(tr("Langue"), "language", [tr("Comme le système")] + [LANGUAGE_NAMES[c] for c in codes[1:]],
                         codes, tr("Appliquée au prochain démarrage")))
        look.add(_choice(tr("File de lecture"), "playlist_side", [tr("À droite"), tr("À gauche")], ["end", "start"],
                         tr("Côté de la fenêtre où s'ouvre la file (F9)")))
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
        page.add(self._library_group())
        page.add(self._tmdb_group())
        return page

    def _library_group(self) -> Adw.PreferencesGroup:
        """Dossiers de la bibliothèque de l'accueil (fonctionnalité « library ») ; aucun choisi : Vidéos."""
        group = Adw.PreferencesGroup(title=tr("Bibliothèque"),
                                     description=tr("Dossiers montrés sur l'accueil quand la fonctionnalité "
                                                    "« Bibliothèque sur l'accueil » est active. Séries : un dossier "
                                                    "par saison (« Série saison 1 »…) ou par série."))
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=tr("Ajouter un dossier…"),
                         css_classes=["flat"], valign=Gtk.Align.CENTER)
        add.connect("clicked", lambda *_: self._add_library_dir())
        group.set_header_suffix(add)
        self.library_group = group
        self._library_rows = []
        self._fill_library_rows()
        return group

    def _tmdb_group(self) -> Adw.PreferencesGroup:
        """Clé d'API TMDB de l'utilisateur (choix de Tof : rien n'est lié à un compte de l'application)."""
        from ..backend import tmdb
        group = Adw.PreferencesGroup(
            title=tr("Affiches de TMDB"),
            description=tr("Avec la fonctionnalité « Affiches de TMDB » : créez un compte gratuit sur "
                           "themoviedb.org, puis copiez ici votre clé d'API (ou votre jeton de lecture). Ce produit "
                           "utilise l'API de TMDB mais n'est ni approuvé ni certifié par TMDB."))
        link = Gtk.LinkButton(uri=tmdb.KEY_PAGE, label=tr("Obtenir une clé"), valign=Gtk.Align.CENTER)
        group.set_header_suffix(link)
        row = Adw.PasswordEntryRow(title=tr("Clé d'API TMDB"), text=settings["tmdb_key"], show_apply_button=True)
        row.connect("apply", lambda r: settings.__setitem__("tmdb_key", r.get_text().strip()))
        group.add(row)
        self.tmdb_row = row
        return group

    def _fill_library_rows(self):
        from ..backend import library
        for row in self._library_rows:
            self.library_group.remove(row)
        self._library_rows = []
        chosen = [d for d in settings["library_dirs"] if isinstance(d, str) and d]
        for d in chosen or library.default_roots():
            row = Adw.ActionRow(title=d, use_markup=False)
            row.add_prefix(Gtk.Image(icon_name="folder-videos-symbolic"))
            if chosen:
                rm = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=tr("Retirer de la bibliothèque"),
                                css_classes=["flat"], valign=Gtk.Align.CENTER)
                rm.connect("clicked", lambda _b, d=d: self._remove_library_dir(d))
                row.add_suffix(rm)
            else:
                row.set_subtitle(tr("Par défaut"))
            self.library_group.add(row)
            self._library_rows.append(row)

    def _add_library_dir(self):
        from ..backend import library
        from .common import choose_folder
        current = [d for d in settings["library_dirs"] if isinstance(d, str) and d] or library.default_roots()

        def chosen(path):
            if path and path not in current:
                settings["library_dirs"] = current + [path]
                self._fill_library_rows()
        choose_folder(self, tr("Ajouter un dossier à la bibliothèque"), current[0] if current else "", chosen)

    def _remove_library_dir(self, path):
        settings["library_dirs"] = [d for d in settings["library_dirs"] if d != path]
        self._fill_library_rows()

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
