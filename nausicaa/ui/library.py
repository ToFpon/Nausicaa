"""Accueil « bibliothèque » (fonctionnalité « library », coupée par défaut ; idée de Tof) : vidéos à reprendre,
séries (une affiche par série, ses saisons, ses épisodes vus) et films, lus dans les dossiers choisis
(Préférences → Général). Sans Internet : affiches posées à côté des vidéos ou tirées des vidéos
(backend/posters.py). Lu dans un fil à chaque retour sur l'accueil."""

from __future__ import annotations

import os
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import format as fmt  # noqa: E402
from ..backend import library, positions, posters  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import choose_files, clear_box, popup_menu, shown  # noqa: E402

CARD_W, CARD_H = 150, 225
RESUME_MAX = 12


def roots() -> list:
    dirs = [d for d in settings["library_dirs"] if isinstance(d, str) and d]
    return dirs or library.default_roots()


def show_key(show) -> str:
    return "show:" + show.name.casefold()


def remaining_text(entry: dict) -> str:
    left = max(0.0, float(entry.get("dur", 0)) - float(entry.get("pos", 0)))
    return tr("reste {0}").format(fmt.long_duration(left)) if left >= 60 else tr("presque fini")


class Card(Gtk.Button):
    """Affiche (2:3), barre d'avancement / coche « vu », titre et sous-titre. Un clic : `on_open` ; clic droit :
    menu de la bibliothèque (`library.card_menu`)."""

    def __init__(self, view, key: str, title: str, subtitle: str = "", progress: float = 0.0, seen: bool = False):
        super().__init__(css_classes=["flat", "library-card"], valign=Gtk.Align.START, halign=Gtk.Align.START,
                         tooltip_text=shown(title))
        self.key = key
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        overlay = Gtk.Overlay(css_classes=["poster"], overflow=Gtk.Overflow.HIDDEN,
                              width_request=CARD_W, height_request=CARD_H)
        self.placeholder = Gtk.Image(icon_name="video-x-generic-symbolic", pixel_size=48,
                                     css_classes=["dim-label"])
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        holder = Gtk.Stack()
        holder.add_child(self.placeholder)
        holder.add_child(self.picture)
        self.holder = holder
        overlay.set_child(holder)
        if progress > 0:
            bar = Gtk.ProgressBar(fraction=min(progress, 1.0), valign=Gtk.Align.END, css_classes=["osd"])
            overlay.add_overlay(bar)
        if seen:
            mark = Gtk.Image(icon_name="object-select-symbolic", halign=Gtk.Align.END, valign=Gtk.Align.START,
                             margin_top=6, margin_end=6, css_classes=["seen-badge"],
                             tooltip_text=tr("Vu"))
            overlay.add_overlay(mark)
        # largeur tenue : un Gtk.Picture demande la taille de son image (300 px) et la grille s'étirait
        clamp = Adw.Clamp(maximum_size=CARD_W, tightening_threshold=CARD_W, halign=Gtk.Align.START)
        clamp.set_child(overlay)
        box.append(clamp)
        label = Gtk.Label(label=shown(title), xalign=0, wrap=True, lines=2, ellipsize=3, max_width_chars=1,
                          width_request=CARD_W, css_classes=["heading"])
        box.append(label)
        if subtitle:
            sub = Gtk.Label(label=shown(subtitle), xalign=0, ellipsize=3, max_width_chars=1, width_request=CARD_W,
                            css_classes=["dim-label", "caption"])
            box.append(sub)
        self.set_child(box)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda g, n, x, y: view.card_menu(self, x, y))
        self.add_controller(click)

    def set_poster(self, path):
        if path and os.path.exists(path):
            self.picture.set_filename(path)
            self.picture.set_visible(True)
            self.holder.set_visible_child(self.picture)


def _flow() -> Gtk.FlowBox:
    return Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=12,
                       row_spacing=18, min_children_per_line=2, max_children_per_line=12,
                       valign=Gtk.Align.START)


class LibraryView(Gtk.Stack):
    """L'accueil bibliothèque : page d'ensemble, et page d'une série (ses saisons)."""

    def __init__(self, window):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150,
                         hexpand=True, vexpand=True)
        self.add_css_class("library")
        self.win = window
        self.lib = library.Library()
        self.entries: dict = {}            # chemin → {pos, dur, seen, time} (positions.json)
        self.show = None                   # série affichée
        self._seq = 0
        self._scanned_at = 0.0
        self._scanning = False
        self.posters: dict = {}            # clé → fichier d'affiche déjà trouvé
        self.worker = posters.PosterWorker()
        self.cards: dict = {}              # clé → [Card…] affichées
        self._menu_card = None
        self._build_home()
        self._build_show_page()
        self._install_actions()

    # -- construction ---------------------------------------------------------------------------
    def _build_home(self):
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=1400, tightening_threshold=1200)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=18, margin_bottom=36,
                      margin_start=24, margin_end=24)
        head = Gtk.Box(spacing=12)
        title = Gtk.Label(label=tr("Bibliothèque"), xalign=0, hexpand=True, css_classes=["title-1"])
        head.append(title)
        self.resume_button = Gtk.Button(action_name="win.restore-queue", visible=False,
                                        css_classes=["pill"], valign=Gtk.Align.CENTER)
        head.append(self.resume_button)
        open_b = Gtk.Button(label=tr("Ouvrir des vidéos…"), action_name="win.open",
                            css_classes=["pill", "suggested-action"], valign=Gtk.Align.CENTER)
        head.append(open_b)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=tr("Relire la bibliothèque"),
                             css_classes=["flat", "circular"], valign=Gtk.Align.CENTER)
        refresh.connect("clicked", lambda *_: self.refresh(force=True))
        head.append(refresh)
        box.append(head)
        self.sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28)
        box.append(self.sections)
        self.empty = Adw.StatusPage(icon_name="folder-videos-symbolic", visible=False,
                                    title=tr("Aucune vidéo dans la bibliothèque"))
        self.empty.add_css_class("compact")
        box.append(self.empty)
        self.loading = Gtk.Spinner(spinning=False, visible=False, halign=Gtk.Align.CENTER,
                                   width_request=32, height_request=32)
        box.append(self.loading)
        clamp.set_child(box)
        scroller.set_child(clamp)
        self.home_scroller = scroller
        self.add_named(scroller, "home")

    def _build_show_page(self):
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=900)
        self.show_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=18,
                                margin_bottom=36, margin_start=24, margin_end=24)
        clamp.set_child(self.show_box)
        scroller.set_child(clamp)
        self.show_scroller = scroller
        self.add_named(scroller, "show")

    def _install_actions(self):
        group = Gio.SimpleActionGroup()
        for name, cb in (("choose-image", self._choose_image), ("forget-image", self._forget_image),
                         ("mark-seen", lambda: self._mark(True)), ("mark-unseen", lambda: self._mark(False))):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            group.add_action(a)
        self.insert_action_group("library", group)

    # -- lecture des dossiers ------------------------------------------------------------------
    def refresh(self, force: bool = False):
        """Relue dans un fil (au retour sur l'accueil : de nouveaux épisodes vus, des fichiers ajoutés)."""
        if self._scanning or (not force and time.monotonic() - self._scanned_at < 2.0):
            return
        self._scanning = True
        self._seq += 1
        seq = self._seq
        dirs = roots()
        known = dict(self.win.c.positions.data)
        if self.lib.empty:
            self.loading.set_visible(True)
            self.loading.set_spinning(True)

        def work():
            try:
                lib = library.scan(dirs)
                entries = {}
                for p in lib.films + [e for s in lib.shows for e in s.episodes]:
                    k = positions.key_for(p)
                    if k in known:
                        entries[p] = known[k]
            except Exception:  # noqa: BLE001 — un dossier illisible ne doit pas casser l'accueil
                import traceback
                traceback.print_exc()
                lib, entries = library.Library(), {}
            GLib.idle_add(lambda: (self._arrived(seq, dirs, lib, entries), False)[1])
        threading.Thread(target=work, daemon=True, name="nausicaa-library").start()

    def _arrived(self, seq, dirs, lib, entries):
        self._scanning = False
        self._scanned_at = time.monotonic()
        self.loading.set_visible(False)
        self.loading.set_spinning(False)
        if seq != self._seq:
            return
        self.lib, self.entries = lib, entries
        self._render_home(dirs)
        if self.show is not None:
            same = next((s for s in lib.shows if s.name.casefold() == self.show.name.casefold()), None)
            if same is None:
                self.go_home()
            else:
                self.open_show(same)

    # -- page d'ensemble ---------------------------------------------------------------------------
    def _section(self, title: str, flow: Gtk.FlowBox):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(Gtk.Label(label=title, xalign=0, css_classes=["title-3"]))
        box.append(flow)
        self.sections.append(box)

    def _entry(self, path) -> dict:
        return self.entries.get(path) or {}

    def _progress(self, entry: dict) -> float:
        dur = float(entry.get("dur") or 0)
        return float(entry.get("pos") or 0) / dur if dur > 0 else 0.0

    def _render_home(self, dirs):
        self.worker.cancel_all()
        clear_box(self.sections)
        self.cards = {}
        lib = self.lib
        self.empty.set_visible(lib.empty)
        if lib.empty:
            self.empty.set_description(shown("\n".join(dirs)) if dirs else tr("Aucun dossier choisi"))
        show_of = {e: s for s in lib.shows for e in s.episodes}
        # à reprendre : commencées, les plus récentes d'abord
        started = sorted((p for p, e in self.entries.items() if float(e.get("pos") or 0) > 0),
                         key=lambda p: self.entries[p].get("time", 0), reverse=True)[:RESUME_MAX]
        if started:
            flow = _flow()
            for p in started:
                e = self.entries[p]
                show = show_of.get(p)
                if show is not None:
                    ep = library.episode_label(p) or library.clean_title(p)
                    card = self._card(show_key(show), show.name, ep + " · " + remaining_text(e),
                                      self._progress(e), poster=("show", show))
                    card.connect("clicked", lambda _b, s=show, path=p: self.play_episode(s, path))
                else:
                    card = self._card(p, library.clean_title(p), remaining_text(e), self._progress(e),
                                      poster=("film", p))
                    card.connect("clicked", lambda _b, path=p: self.play_film(path))
                flow.append(card)
            self._section(tr("Reprendre"), flow)
        if lib.shows:
            flow = _flow()
            for s in lib.shows:
                eps = s.episodes
                seen = sum(1 for p in eps if self._entry(p).get("seen"))
                seasons = [x for x in s.seasons if x.number is not None]
                sub = (ntr("{0} saison", "{0} saisons", len(seasons)).format(len(seasons)) + " · " if seasons else "")
                sub += ntr("{0} épisode", "{0} épisodes", len(eps)).format(len(eps))
                card = self._card(show_key(s), s.name, sub, seen=bool(eps) and seen == len(eps), poster=("show", s))
                card.connect("clicked", lambda _b, show=s: self.open_show(show))
                flow.append(card)
            self._section(tr("Séries"), flow)
        if lib.films:
            flow = _flow()
            for p in lib.films:
                e = self._entry(p)
                card = self._card(p, library.clean_title(p), "", self._progress(e), seen=bool(e.get("seen")),
                                  poster=("film", p))
                card.connect("clicked", lambda _b, path=p: self.play_film(path))
                flow.append(card)
            self._section(tr("Films"), flow)

    def _card(self, key, title, subtitle="", progress=0.0, seen=False, poster=None) -> Card:
        card = Card(self, key, title, subtitle, progress, seen)
        self.cards.setdefault(key, []).append(card)
        if key in self.posters:
            card.set_poster(self.posters[key])
        elif poster is not None:
            self._want_poster(key, *poster)
        return card

    def _want_poster(self, key, kind, target):
        chosen = settings["library_images"].get(key, "")
        if kind == "show":
            folders, video, alone = target.folders, (target.episodes[0] if target.episodes else ""), False
            args = ("show", folders, video, alone, chosen)
        else:
            folder = os.path.dirname(target)
            alone = folder not in [os.path.abspath(r) for r in roots()] and \
                sum(1 for p in self.lib.films if os.path.dirname(p) == folder) == 1
            args = ("film", target, target, alone, chosen)

        def got(path, k=key):
            if path:
                self.posters[k] = path
                for c in self.cards.get(k, []):
                    c.set_poster(path)
        self.worker.request(key, lambda: posters.poster_for(*args), got)

    # -- page d'une série ---------------------------------------------------------------------------
    def open_show(self, show):
        self.show = show
        box = self.show_box
        clear_box(box)
        key = show_key(show)
        self.cards.pop(key + "#page", None)
        head = Gtk.Box(spacing=18)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=tr("Retour"), valign=Gtk.Align.START,
                          css_classes=["flat", "circular"])
        back.connect("clicked", lambda *_: self.go_home())
        head.append(back)
        card = Card(self, key, "", "")
        card.set_can_target(False)
        card.get_child().remove(card.get_child().get_last_child())      # pas de titre sous l'affiche ici
        self.cards.setdefault(key, []).append(card)
        if key in self.posters:
            card.set_poster(self.posters[key])
        else:
            self._want_poster(key, "show", show)
        head.append(card)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER, hexpand=True)
        info.append(Gtk.Label(label=shown(show.name), xalign=0, wrap=True, css_classes=["title-1"]))
        eps = show.episodes
        seen = sum(1 for p in eps if self._entry(p).get("seen"))
        info.append(Gtk.Label(label=ntr("{0} épisode vu sur {1}", "{0} épisodes vus sur {1}", seen).format(seen, len(eps)),
                              xalign=0,
                              css_classes=["dim-label"]))
        nxt = self.next_episode(show)
        if nxt is not None:
            e = self._entry(nxt)
            ep = library.episode_label(nxt) or library.clean_title(nxt)
            label = (tr("Reprendre {0}") if float(e.get("pos") or 0) > 0 else tr("Lire {0}")).format(ep)
            play = Gtk.Button(label=shown(label), halign=Gtk.Align.START, css_classes=["pill", "suggested-action"])
            play.connect("clicked", lambda *_: self.play_episode(show, nxt))
            play.get_child().set_ellipsize(3)
            info.append(play)
        head.append(info)
        box.append(head)
        for season in show.seasons:
            title = (tr("Saison {0}").format(season.number) if season.number is not None else season.label)
            group = Adw.PreferencesGroup(title=shown(title))
            for p in season.episodes:
                e = self._entry(p)
                row = Adw.ActionRow(title=shown(os.path.splitext(os.path.basename(p))[0]), use_markup=False,
                                    activatable=True)
                row.set_title_lines(2)
                pos = float(e.get("pos") or 0)
                if pos > 0:
                    row.set_subtitle(remaining_text(e))
                    bar = Gtk.ProgressBar(fraction=self._progress(e), valign=Gtk.Align.CENTER, width_request=80)
                    row.add_suffix(bar)
                if e.get("seen"):
                    row.add_suffix(Gtk.Image(icon_name="object-select-symbolic", tooltip_text=tr("Vu"),
                                             css_classes=["seen-mark"]))
                icon = Gtk.Image(icon_name="media-playback-start-symbolic")
                row.add_prefix(icon)
                row.connect("activated", lambda _r, path=p: self.play_episode(show, path))
                row._path = p
                click = Gtk.GestureClick(button=3)
                click.connect("pressed", lambda g, n, x, y, r=row: self.row_menu(r, x, y))
                row.add_controller(click)
                group.add(row)
            box.append(group)
        self.set_visible_child_name("show")
        self.show_scroller.get_vadjustment().set_value(0)

    def go_home(self):
        self.show = None
        self.set_visible_child_name("home")

    def next_episode(self, show):
        """Épisode commencé le plus récent, sinon le premier non vu après le dernier vu, sinon le premier."""
        eps = show.episodes
        started = [p for p in eps if float(self._entry(p).get("pos") or 0) > 0]
        if started:
            return max(started, key=lambda p: self._entry(p).get("time", 0))
        last = max((i for i, p in enumerate(eps) if self._entry(p).get("seen")), default=-1)
        for p in eps[last + 1:]:
            if not self._entry(p).get("seen"):
                return p
        return eps[0] if eps else None

    # -- lecture -------------------------------------------------------------------------------
    def play_film(self, path: str):
        self.win.open_paths([path], start=path)

    def play_episode(self, show, path: str):
        """La série entière dans la file, à partir de cet épisode (« Suivant » passe au suivant, saisons
        comprises)."""
        self.win.open_paths(show.episodes, start=path)

    # -- clic droit ------------------------------------------------------------------------------
    def card_menu(self, card: Card, x: float, y: float):
        self._menu_card = card
        self._menu_path = card.key if not card.key.startswith("show:") else None
        menu = Gio.Menu()
        images = Gio.Menu()
        images.append(tr("Choisir une image…"), "library.choose-image")
        if card.key in settings["library_images"]:
            images.append(tr("Image d'origine"), "library.forget-image")
        menu.append_section(None, images)
        if self._menu_path:
            menu.append_section(None, self._seen_menu(self._menu_path))
        self._popup(card, menu, x, y)

    def row_menu(self, row, x, y):
        self._menu_card = None
        self._menu_path = row._path
        self._popup(row, self._seen_menu(row._path), x, y)

    def _seen_menu(self, path) -> Gio.Menu:
        m = Gio.Menu()
        if self._entry(path).get("seen"):
            m.append(tr("Marquer comme non vu"), "library.mark-unseen")
        else:
            m.append(tr("Marquer comme vu"), "library.mark-seen")
        return m

    def _popup(self, widget, menu, x, y):
        """Un seul menu, posé sur la page (jamais sur une carte : elles sont reconstruites à chaque lecture,
        un menu détruit juste après sa fermeture plantait GTK 4.14)."""
        anchor = self.get_visible_child()
        ok = widget.translate_coordinates(anchor, x, y)
        px, py = (ok[1], ok[2]) if isinstance(ok, tuple) and len(ok) == 3 else (ok if ok else (x, y))
        popup_menu(anchor, menu, px, py)

    def _choose_image(self):
        card = self._menu_card
        if card is None:
            return
        key = card.key

        def chosen(paths):
            if not paths:
                return
            images = dict(settings["library_images"])
            images[key] = paths[0]
            settings["library_images"] = images
            self.posters.pop(key, None)
            self.refresh(force=True)
        start = os.path.dirname(key) if not key.startswith("show:") else (roots() or [""])[0]
        choose_files(self.win, tr("Choisir une image"), start, chosen,
                     filters=[(tr("Images"), ["*.jpg", "*.jpeg", "*.png", "*.webp", "*.JPG", "*.PNG"])],
                     multiple=False)

    def _forget_image(self):
        card = self._menu_card
        if card is None:
            return
        images = dict(settings["library_images"])
        images.pop(card.key, None)
        settings["library_images"] = images
        self.posters.pop(card.key, None)
        self.refresh(force=True)

    def _mark(self, seen: bool):
        path = getattr(self, "_menu_path", None)
        if not path:
            return
        pos = self.win.c.positions
        if seen:
            pos.finished(path)
        else:
            k = positions.key_for(path)
            if k:
                pos.data.pop(k, None)
        pos.save()
        self.refresh(force=True)
