"""Fenêtre principale : la vidéo, la barre de titre en haut, les commandes en bas, la file à droite.

Fenêtré : barres toujours visibles, l'image n'est jamais cachée par elles. Plein écran : l'image
prend tout l'écran, les barres et le pointeur apparaissent au mouvement de la souris et s'effacent
après HIDE_DELAY ms d'immobilité pendant la lecture (jamais tant que le pointeur est sur une barre ou
qu'un menu est ouvert).

Souris sur l'image (comme VLC, en mieux) :
- clic gauche : pause / lecture tout de suite (fonctionnalité « click_pause ») ;
- double-clic : plein écran, SANS pause — le premier clic a déjà basculé la pause, le second la
  rétablit (le délai d'un double-clic, ~0,3 s, est invisible) ; attendre la fin du délai pour
  décider aurait rendu chaque pause molle ;
- molette : volume (« wheel_volume »), affiché par-dessus l'image ;
- clic droit : menu.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID, APP_NAME, features  # noqa: E402
from ..format import duration as fmt  # noqa: E402
from ..backend import credits as credits_mod  # noqa: E402
from ..i18n import decimal, ntr, tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import alert, choose_files, choose_folder, popup_menu, shown, toast  # noqa: E402
from .controls import Controls, volume_icon  # noqa: E402
from .osd import Osd  # noqa: E402
from .playlist_panel import PlaylistPanel  # noqa: E402
from .video import VideoView  # noqa: E402

HIDE_DELAY = 2000
SPEEDS = ("0.5", "0.75", "1.0", "1.25", "1.5", "2.0")


def speed_text(speed: float) -> str:
    """« × 1,5 », « × 0,75 », « × 2 » (virgule en français et en allemand)."""
    return "× " + decimal(speed, 2).rstrip("0").rstrip(",.")


def _media_filters():
    from ..backend.media import MEDIA_EXTS
    from ..backend.playlists import READ_EXTS
    return [(tr("Vidéos et sons"), ["*" + e for e in MEDIA_EXTS] + ["*" + e.upper() for e in MEDIA_EXTS]),
            (tr("Listes de lecture"), ["*" + e for e in READ_EXTS]),
            (tr("Tous les fichiers"), ["*"])]


class PictureArea(Gtk.Overlay):
    """L'image, avec la page d'accueil et la page « son seul » posées dessus.

    Pas un Gtk.Stack : un Stack ne réalise que la page affichée, et sans GLArea réalisée mpv n'a pas
    de contexte de rendu — le premier fichier ouvert depuis l'accueil échouait (« ne peut pas être
    lu »). La vidéo reste donc toujours en place, dessous.

    Accueil et « son seul » : ni fond noir ni image (vidéo à opacité 0, toujours réalisée), le fond de
    la fenêtre du thème se voit — comme Ithaca (demande de Tof : son violet translucide / flouté)."""

    def __init__(self, video: Gtk.Widget, pages: dict):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("video-area")
        self.video = video
        self.set_child(video)
        self.pages = pages
        for page in pages.values():
            page.set_visible(False)
            self.add_overlay(page)
        self._name = "video"

    def get_visible_child_name(self) -> str:
        return self._name

    def set_visible_child_name(self, name: str):
        self._name = name
        for key, page in self.pages.items():
            page.set_visible(key == name)
        on_page = name in self.pages
        self.video.set_opacity(0 if on_page else 1)
        if on_page:
            self.add_css_class("page")
        else:
            self.remove_css_class("page")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, icon_name=APP_ID)
        self.add_css_class("nausicaa-window")
        self.app = app
        self.c = app.controller
        self.set_default_size(settings["window_width"], settings["window_height"])
        if settings["window_maximized"]:
            self.maximize()                      # avant l'affichage : ouverte directement maximisée
        self.set_size_request(480, 320)
        self._click_toggled = False
        self._opening = 0                        # ouvertures en cours : image noire, pas l'accueil
        self._fs_pending = False                 # « Plein écran à l'ouverture » : en attente de la piste vidéo
        self.pip = False                          # image dans l'image
        self._pip_target = (0, 0)
        self._pip_frame = (0, 0)
        self._pip_saved =(settings["window_width"], settings["window_height"], False, False)
        self._wheel_acc = 0.0
        self._hide_timer = 0
        self._over_bars = False
        self._paused_by_minimize = False
        self._surface_handler = None
        self._last_motion = (0.0, 0.0)
        # générique de fin (fonctionnalité « skip_credits ») : chapitres, sinon durée apprise par série
        self.credits_store = credits_mod.CreditsStore()
        self._credits = None                     # credits_mod.Credits de la vidéo en cours, ou None
        self._credits_path = None
        self._credits_loaded = False             # le fichier de l'élément courant est-il ouvert par mpv ?
        self._credits_done = None                # vidéo dont le générique a été passé / refusé (une fois)

        # -- image -----------------------------------------------------------------------
        self.video = VideoView(self.c.player.mpv)
        self.video.add_css_class("video")
        self.welcome = self._build_welcome()
        self.audio_page = Adw.StatusPage(icon_name="audio-x-generic-symbolic")
        self.audio_page.add_css_class("audio-only")
        self.picture = PictureArea(self.video, {"welcome": self.welcome, "audio": self.audio_page})
        self.osd = Osd()
        overlay = Gtk.Overlay()
        overlay.set_child(self.picture)
        overlay.add_overlay(self.osd)
        self.drop_hint = Adw.StatusPage(icon_name="video-x-generic-symbolic", title=tr("Déposez pour lire"),
                                        visible=False, can_target=False)
        self.drop_hint.add_css_class("drop-hint")
        overlay.add_overlay(self.drop_hint)
        self.picture_overlay = overlay
        # plein écran : la barre de commandes vient ici, flottante, centrée en bas de l'image
        self.float_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                           transition_duration=200, valign=Gtk.Align.END,
                                           halign=Gtk.Align.CENTER, margin_bottom=32, reveal_child=False)
        overlay.add_overlay(self.float_revealer)
        self._build_credits_button(overlay)
        self._build_pip_overlays(overlay)

        # -- barres ----------------------------------------------------------------------
        self.header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title=APP_NAME)
        self.header.set_title_widget(self.title_widget)
        open_button = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=tr("Ouvrir des vidéos"),
                                 action_name="win.open")
        self.header.pack_start(open_button)
        self.main_menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self._main_menu(),
                                               tooltip_text=tr("Menu principal"), primary=True)
        self.header.pack_end(self.main_menu_button)
        self.controls = Controls(self.c)
        self.controls.tracks.set_menu_model(Gio.Menu())
        self.toolbar = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT, bottom_bar_style=Adw.ToolbarStyle.RAISED)
        self.toolbar.add_top_bar(self.header)
        self.toolbar.add_bottom_bar(self.controls)
        self.toolbar.set_content(overlay)
        for bar in (self.header, self.controls):
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", lambda *_: setattr(self, "_over_bars", True))
            hover.connect("leave", lambda *_: setattr(self, "_over_bars", False))
            bar.add_controller(hover)

        self.panel = PlaylistPanel(self.c)
        self.split = Adw.OverlaySplitView(sidebar_position=self._playlist_side(),
                                          show_sidebar=settings["playlist_visible"],
                                          min_sidebar_width=300, max_sidebar_width=440, sidebar_width_fraction=0.34)
        self.split.set_content(self.toolbar)
        self.split.set_sidebar(self.panel)
        self.split.connect("notify::show-sidebar", self._on_sidebar)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.split)
        self.set_content(self.toast_overlay)

        self._install_actions()
        self._install_mouse()
        self._install_drop()
        help_key = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        help_key.connect("key-pressed", self._on_help_key)
        self.add_controller(help_key)
        list_keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        list_keys.connect("key-pressed", self._on_list_key)
        self.add_controller(list_keys)
        self.c.connect(self._on_event)
        settings.connect(self._on_setting)
        self.connect("notify::fullscreened", self._on_fullscreen)
        self.connect("close-request", self._on_close_request)
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
        self._sync_all()

    # -- construction -----------------------------------------------------------------------
    def _build_pip_overlays(self, overlay: Gtk.Overlay):
        """Image dans l'image, au passage de la souris : petite capsule (précédent, lecture, suivant) en
        bas, bouton pour revenir en haut à gauche (demande de Tof)."""
        def button(icon, tooltip, action):
            b = Gtk.Button(icon_name=icon, tooltip_text=tooltip, action_name=action, valign=Gtk.Align.CENTER)
            b.add_css_class("flat")
            b.add_css_class("circular")
            return b
        box = Gtk.Box(spacing=4)
        for cls in ("nausicaa-controls", "floating-controls", "pip-controls"):
            box.add_css_class(cls)
        box.append(button("media-skip-backward-symbolic", tr("Précédent"), "win.previous"))
        self.pip_play = button("media-playback-start-symbolic", tr("Lire"), "win.toggle")
        box.append(self.pip_play)
        box.append(button("media-skip-forward-symbolic", tr("Suivant"), "win.next"))
        self.pip_controls = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                         transition_duration=150, valign=Gtk.Align.END,
                                         halign=Gtk.Align.CENTER, margin_bottom=10, reveal_child=False,
                                         child=box)
        leave = button("nausicaa-pip-symbolic", tr("Quitter l'image dans l'image"), "win.pip")
        for cls in ("nausicaa-controls", "floating-controls", "pip-controls"):
            leave.add_css_class(cls)
        self.pip_leave = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                      transition_duration=150, valign=Gtk.Align.START,
                                      halign=Gtk.Align.START, margin_top=8, margin_start=8,
                                      reveal_child=False, child=leave)
        for r in (self.pip_controls, self.pip_leave):
            overlay.add_overlay(r)
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", lambda *_: setattr(self, "_over_bars", True))
            hover.connect("leave", lambda *_: setattr(self, "_over_bars", False))
            r.add_controller(hover)

    def _build_credits_button(self, overlay: Gtk.Overlay):
        """« Épisode suivant » en bas à droite de l'image pendant le générique de fin (comme les plateformes de
        streaming), avec une croix pour continuer à regarder. Reste visible en plein écran, barres effacées."""
        # discret (demande de Tof) : bouton plat dans la capsule translucide du panneau du plein écran, pas un
        # bouton bleu plein ; icône « suivant » + texte
        self.credits_content = Adw.ButtonContent(icon_name="media-skip-forward-symbolic", label=tr("Épisode suivant"))
        self.credits_button = Gtk.Button(child=self.credits_content)
        self.credits_button.add_css_class("flat")
        self.credits_button.connect("clicked", lambda *_: self.skip_credits())
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=tr("Continuer à regarder"),
                           valign=Gtk.Align.CENTER)
        close.add_css_class("flat")
        close.add_css_class("circular")
        close.connect("clicked", lambda *_: self._dismiss_credits())
        box = Gtk.Box(spacing=6)
        for cls in ("nausicaa-controls", "floating-controls", "credits-skip"):
            box.add_css_class(cls)
        box.append(self.credits_button)
        box.append(close)
        self.credits_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                             transition_duration=200, valign=Gtk.Align.END, halign=Gtk.Align.END,
                                             margin_bottom=24, margin_end=24, reveal_child=False, child=box)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: setattr(self, "_over_bars", True))
        hover.connect("leave", lambda *_: setattr(self, "_over_bars", False))
        self.credits_revealer.add_controller(hover)
        overlay.add_overlay(self.credits_revealer)

    # -- générique de fin ---------------------------------------------------------------------
    def _find_credits(self):
        """Générique de la vidéo en cours (chapitres, sinon durée apprise pour la série)."""
        item = self.c.item
        path = item.path if item is not None and not item.stream else None
        # « déjà passé / refusé » vaut pour CET élément de la file : la même vidéo rouverte repropose
        self._credits_path = item if path is not None else None
        self._credits = None
        # juste après un changement d'élément, mpv a encore l'ANCIEN fichier (sa position, sa durée) : le
        # bouton, ou le passage automatique, partait sur des valeurs périmées — on attend « loaded »
        if path is not None and self._credits_loaded and features.enabled("skip_credits"):
            self._credits = credits_mod.find(path, self.c.player.duration(), self.c.player.get("chapter-list"),
                                             self.credits_store)
        self._sync_credits(self.c.player.position())

    def _credits_target(self) -> str:
        """« seek » (scène après le générique), « next » (épisode suivant), "" (rien à proposer)."""
        cr = self._credits
        if cr is None:
            return ""
        if cr.resume is not None:
            return "seek"
        q = self.c.queue
        return "next" if len(q) > 1 and q.next_index() is not None and q.repeat != "one" else ""

    def credits_showing(self) -> bool:
        return self.credits_revealer.get_reveal_child()

    def _sync_credits(self, pos: float):
        cr, target = self._credits, self._credits_target()
        dur = self.c.player.duration()
        show = (cr is not None and bool(target) and not self.pip and self._credits_done is not self._credits_path
                and cr.start <= pos < (dur or 0) - 0.5)
        if show and settings["credits_auto"]:
            self.skip_credits()
            return
        if show:
            self.credits_content.set_label(tr("Passer le générique") if target == "seek" else tr("Épisode suivant"))
            self.credits_revealer.set_margin_bottom(110 if self.is_fullscreen() else 24)
        if self.credits_revealer.get_reveal_child() != show:
            self.credits_revealer.set_reveal_child(show)

    def skip_credits(self):
        target = self._credits_target()
        self._credits_done = self._credits_path
        self.credits_revealer.set_reveal_child(False)
        if target == "seek":
            self.c.seek_to(self._credits.resume)
        elif target == "next":
            self.c.next()

    def _dismiss_credits(self):
        self._credits_done = self._credits_path
        self.credits_revealer.set_reveal_child(False)

    def mark_credits(self):
        """« Le générique commence ici » (G, clic droit) : la durée qui reste est retenue pour toute la série."""
        item = self.c.item
        if item is None or item.stream:
            return
        before = self.credits_store.get(item.path)
        left = self.credits_store.learn(item.path, self.c.player.position(), self.c.player.duration())
        if left is None:
            toast(self, tr("Placez-vous au début du générique de fin, dans la seconde moitié de l'épisode"))
            return
        name = shown(credits_mod.series_name(item.path))
        self._credits_done = self._credits_path       # on regarde ce générique-ci : pas de bouton tout de suite
        self._find_credits()

        def undo():
            self.credits_store.put_back(item.path, before)
            self._find_credits()
        toast(self, tr("Générique retenu pour « {0} » : {1} avant la fin").format(name, fmt(left)),
              tr("Annuler"), undo, timeout=6)

    def forget_credits(self):
        item = self.c.item
        if item is None or item.stream:
            return
        old = self.credits_store.forget(item.path)
        if old is None:
            return
        self._find_credits()

        def undo():
            self.credits_store.put_back(item.path, old)
            self._find_credits()
        toast(self, tr("Générique oublié pour « {0} »").format(shown(credits_mod.series_name(item.path))),
              tr("Annuler"), undo, timeout=6)

    def _reveal_pip_overlays(self, reveal: bool):
        self.pip_controls.set_reveal_child(reveal and self.pip)
        self.pip_leave.set_reveal_child(reveal and self.pip)

    def _build_welcome(self) -> Gtk.Widget:
        """Accueil : la page classique, ou la bibliothèque (fonctionnalité « library », suivie en direct)."""
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        stack.add_css_class("welcome")
        stack.add_named(self._build_classic_welcome(), "classic")
        self.library = None                      # LibraryView, créée la première fois qu'elle sert
        self.welcome_stack = stack
        return stack

    def _sync_welcome(self):
        """Page d'accueil affichée : bibliothèque (relue) si la fonctionnalité est active, sinon la classique."""
        if features.enabled("library"):
            if self.library is None:
                from .library import LibraryView
                self.library = LibraryView(self)
                self.welcome_stack.add_named(self.library, "library")
                self._sync_resume_button()
            self.welcome_stack.set_visible_child_name("library")
            if self.picture.get_visible_child_name() == "welcome":
                self.library.refresh()
        else:
            self.welcome_stack.set_visible_child_name("classic")

    def _sync_tmdb(self, force: bool = False):
        """Affiches de TMDB activées / coupées (ou autre clé) : la bibliothèque les redemande."""
        if self.library is None:
            return
        from .library import tmdb_on
        on = tmdb_on()
        if force or on != self.library._tmdb_was:
            self.library._tmdb_was = on
            self.library.reset_posters()

    def _build_classic_welcome(self) -> Gtk.Widget:
        page = Adw.StatusPage(icon_name=APP_ID, title=APP_NAME,
                              description=tr("Déposez des vidéos ici, ou ouvrez-les"))
        page.add_css_class("welcome")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        open_b = Gtk.Button(label=tr("Ouvrir des vidéos…"), action_name="win.open")
        open_b.add_css_class("pill")
        open_b.add_css_class("suggested-action")
        box.append(open_b)
        self.resume_button = Gtk.Button(action_name="win.restore-queue", visible=False)
        self.resume_button.add_css_class("pill")
        box.append(self.resume_button)
        page.set_child(box)
        return page

    def _main_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        files = Gio.Menu()
        files.append(tr("Ouvrir des vidéos…"), "win.open")
        files.append(tr("Ouvrir un dossier…"), "win.open-folder")
        files.append(tr("Ouvrir une adresse…"), "win.open-location")
        menu.append_section(None, files)
        tools = Gio.Menu()
        tools.append(tr("Image dans l'image"), "win.pip")
        tools.append(tr("Capture de l'image"), "win.screenshot")
        tools.append(tr("Afficher la file de lecture"), "win.playlist")
        menu.append_section(None, tools)
        app = Gio.Menu()
        app.append(tr("Préférences"), "app.preferences")
        app.append(tr("Raccourcis clavier"), "win.show-help-overlay")
        app.append(tr("À propos de Nausicaa"), "app.about")
        app.append(tr("Quitter"), "app.quit")
        menu.append_section(None, app)
        return menu

    def _install_actions(self):
        c = self.c
        app = self.get_application()
        simple = (
            ("open", self.open_dialog, ["<Control>o"]),
            ("open-folder", self.open_folder_dialog, ["<Control><Shift>o"]),
            ("open-location", self.open_location_dialog, ["<Control>u"]),
            ("restore-queue", lambda: (self._want_fullscreen(),
                                       self.video.when_ready(lambda: self._gl_failed() or c.restore_saved())), []),
            ("toggle", self.toggle_pause, ["space", "k", "AudioPlay", "AudioPause"]),
            ("next", c.next, ["n", "Page_Down", "AudioNext"]),
            ("previous", c.previous, ["p", "Page_Up", "AudioPrev"]),
            ("fullscreen", self.toggle_fullscreen, ["f", "F11"]),
            ("leave-fullscreen", self.leave_fullscreen, ["Escape"]),
            ("seek-forward", lambda: self.seek(settings["seek_short"]), ["Right"]),
            ("seek-backward", lambda: self.seek(-settings["seek_short"]), ["Left"]),
            ("seek-forward-long", lambda: self.seek(settings["seek_long"]), ["<Control>Right"]),
            ("seek-backward-long", lambda: self.seek(-settings["seek_long"]), ["<Control>Left"]),
            ("volume-up", lambda: self.change_volume(settings["wheel_step"]), ["Up", "plus", "KP_Add"]),
            ("volume-down", lambda: self.change_volume(-settings["wheel_step"]), ["Down", "minus", "KP_Subtract"]),
            ("mute", self.toggle_mute, ["m", "AudioMute"]),
            ("frame-next", lambda: self.frame_step(False), ["period"]),
            ("frame-previous", lambda: self.frame_step(True), ["comma"]),
            ("screenshot", self.screenshot, ["s"]),
            ("subtitles-visible", self.toggle_subtitles, ["v"]),
            ("credits-here", self.mark_credits, ["g"]),
            ("credits-forget", self.forget_credits, []),
            ("credits-skip", self.skip_credits, []),
            ("add-subtitles", self.add_subtitles_dialog, []),
            ("save-playlist", self.panel.save_queue, ["<Control>s"]),
            ("clear-queue", c.clear, []),
            # F1 en premier (c'est lui que le menu affiche) : il marche sur tous les claviers. Ctrl+? reste,
            # avec un filet pour les dispositions où GTK ne le reconnaît pas (_on_help_key)
            ("show-help-overlay", self.show_shortcuts, ["F1", "<Control>question"]),
        )
        for name, cb, accels in simple:
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
            if accels:
                app.set_accels_for_action(f"win.{name}", accels)
        self.playlist_action = Gio.SimpleAction.new_stateful("playlist", None,
                                                             GLib.Variant("b", settings["playlist_visible"]))
        self.playlist_action.connect("activate", lambda a, _p: self.split.set_show_sidebar(
            not a.get_state().get_boolean()))
        self.add_action(self.playlist_action)
        app.set_accels_for_action("win.playlist", ["F9", "<Control>l"])
        pip = Gio.SimpleAction.new_stateful("pip", None, GLib.Variant("b", False))
        pip.connect("activate", lambda *_: self.toggle_pip())
        self.add_action(pip)
        app.set_accels_for_action("win.pip", ["<Control>p"])
        for name, cb in (("audio-track", self._on_audio_track), ("subtitle-track", self._on_subtitle_track),
                         ("speed", self._on_speed)):
            a = Gio.SimpleAction.new_stateful(name, GLib.VariantType.new("s"), GLib.Variant("s", ""))
            a.connect("change-state", cb)
            self.add_action(a)
        self.lookup_action("speed").set_state(GLib.Variant("s", "1.0"))

    def _install_mouse(self):
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._on_click)
        self.picture.add_controller(click)
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        self.picture.add_controller(drag)
        edges = Gtk.EventControllerMotion()            # image dans l'image : curseur près des bords
        edges.connect("motion", self._on_picture_motion)
        self.picture.add_controller(edges)
        self._drag_start = (0.0, 0.0)
        self._dragging_window = False
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", self._on_right_click)
        self.picture.add_controller(right)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self._on_wheel)
        self.picture_overlay.add_controller(wheel)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_pointer_leave)
        self.toolbar.add_controller(motion)

    def _on_list_key(self, _ctl, keyval, _keycode, state):
        """Focus dans la file : ↑ / ↓ / Début / Fin y naviguent. Les raccourcis de l'application passent
        AVANT la liste (GTK 4) : ↓ baissait le volume et la sélection ne bougeait pas."""
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        focus = self.get_focus()
        lst = self.panel.list
        if focus is None or not (focus is lst or focus.is_ancestor(lst)):
            return False
        rows = []
        i = 0
        while (row := lst.get_row_at_index(i)) is not None:
            rows.append(row)
            i += 1
        if not rows:
            return False
        current = next((r for r in rows if focus is r or focus.is_ancestor(r)), None)
        index = rows.index(current) if current is not None else -1
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            target = rows[min(index + 1, len(rows) - 1)]
        elif keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            target = rows[max(index - 1, 0)]
        elif keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            target = rows[0]
        elif keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
            target = rows[-1]
        else:
            return False
        target.grab_focus()
        if not state & Gdk.ModifierType.SHIFT_MASK:
            lst.unselect_all()
        lst.select_row(target)
        return True

    def _on_help_key(self, ctl, keyval, keycode, state):
        """Ctrl+? quelle que soit la disposition du clavier. Signalé par un utilisateur allemand (QWERTZ,
        « ? » = Maj+ß) : rien ne se passait. Reproduit nulle part sous X11 (us, de, fr passent) — sans doute
        Wayland, où GTK 4.14 rapproche mal certains raccourcis à Maj. On regarde le caractère que donne
        vraiment la touche sur la disposition active, Ctrl retiré : « ? » → fenêtre des raccourcis."""
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        typed = keyval
        display = self.get_display()
        event = ctl.get_current_event()
        group = event.get_layout() if event is not None else 0
        try:
            ok, typed, *_ = display.translate_key(keycode, state & ~Gdk.ModifierType.CONTROL_MASK, group)
            if not ok:
                typed = keyval
        except (TypeError, ValueError, AttributeError):
            typed = keyval
        if Gdk.keyval_to_unicode(typed) == ord("?") or Gdk.keyval_to_unicode(keyval) == ord("?"):
            self.show_shortcuts()
            return True
        return False

    def _install_drop(self):
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("enter", lambda *_: (self.drop_hint.set_visible(True), Gdk.DragAction.COPY)[1])
        target.connect("leave", lambda *_: self.drop_hint.set_visible(False))
        target.connect("drop", self._on_drop)
        self.picture_overlay.add_controller(target)

    # -- ouverture --------------------------------------------------------------------------
    def open_paths(self, paths: list[str], replace: bool = True, start=None):
        # en attendant la vidéo (contexte de rendu, lecture du dossier dans un fil) : image noire plutôt que
        # l'accueil — ouverte de l'extérieur (Odysseus, « Ouvrir avec »), l'accueil passait une fraction de
        # seconde avant (vu chez Tof, comme dans Argos) ; rien de lisible : retour à l'accueil
        self._opening += 1
        self._sync_page()

        def done(n):
            self._opening -= 1
            self._sync_page()
            if not n:
                toast(self, tr("Aucune vidéo trouvée"))

        def start_open():
            if self._gl_failed():
                done(1)                          # message déjà donné par _gl_failed
            else:
                self.c.open_paths(paths, replace=replace, on_done=done, start=start)
        if replace:
            self._want_fullscreen()
        self.video.when_ready(start_open)

    def _want_fullscreen(self):
        """Ouverture par l'utilisateur : plein écran dès que la piste vidéo est connue (option, comme VLC).
        Les vidéos suivantes de la file n'y sont pas remises de force (Échap reste un choix)."""
        self._fs_pending = features.enabled("auto_fullscreen")

    def _auto_fullscreen(self):
        if not self._fs_pending or self.c.player.path is None:
            return
        tracks = (self.c.player.values.get("track-list") or [])
        if not tracks:
            return                               # pas encore lu : on attend la liste des pistes
        self._fs_pending = False
        # une pochette d'album (MP3, FLAC) est une « piste vidéo » pour mpv : pas un film
        if any(t.get("type") == "video" and not t.get("albumart") for t in tracks) \
                and not self.pip and not self.is_fullscreen():
            self.fullscreen()

    def _gl_failed(self) -> bool:
        """OpenGL indisponible (pilote cassé, X déporté par ssh sans GLX) : mpv ne peut rien afficher.
        Un seul message clair au lieu d'un « ne peut pas être lu » par fichier de la file."""
        err = self.video.gl_error
        if err is None:
            return False
        if not getattr(self, "_gl_told", False):
            self._gl_told = True
            alert(self, tr("Affichage vidéo impossible"),
                  tr("OpenGL n'est pas disponible sur cet ordinateur : {0}").format(shown(err)))
        return True

    def _start_folder(self) -> str:
        item = self.c.item
        if item is not None and not item.stream and os.path.isdir(os.path.dirname(item.path)):
            return os.path.dirname(item.path)
        videos = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_VIDEOS)
        return videos if videos and os.path.isdir(videos) else os.path.expanduser("~")

    def open_dialog(self):
        choose_files(self, tr("Ouvrir des vidéos"), self._start_folder(), self.open_paths,
                     filters=_media_filters())

    def open_folder_dialog(self):
        choose_folder(self, tr("Ouvrir un dossier"), self._start_folder(), lambda p: self.open_paths([p]))

    def open_location_dialog(self):
        entry = Gtk.Entry(placeholder_text="https://…", activates_default=True, width_chars=40)
        clip = self.get_clipboard()

        def got_text(cb, res):
            try:
                text = cb.read_text_finish(res) or ""
            except GLib.Error:
                return
            if "://" in text and not entry.get_text():
                entry.set_text(text.strip())
                entry.select_region(0, -1)
        clip.read_text_async(None, got_text)

        def answered(response):
            url = entry.get_text().strip()
            if response == "open" and url:
                self.open_paths([url])
        alert(self, tr("Ouvrir une adresse"), tr("Adresse d'une vidéo, d'un flux ou d'une liste de lecture"),
              responses=(("cancel", tr("Annuler")), ("open", tr("Ouvrir"))), default="open", suggested="open",
              callback=answered, extra=entry)

    def add_subtitles_dialog(self):
        from ..backend.media import SUBTITLE_EXTS
        if self.c.item is None:
            return
        choose_files(self, tr("Ajouter des sous-titres"), self._start_folder(),
                     lambda paths: self.c.player.add_subtitle(paths[0]), multiple=False,
                     filters=[(tr("Sous-titres"), ["*" + e for e in SUBTITLE_EXTS]), (tr("Tous les fichiers"), ["*"])])

    def _on_drop(self, _target, value, _x, _y):
        self.drop_hint.set_visible(False)
        paths = [f.get_path() or f.get_uri() for f in value.get_files()]
        paths = [p for p in paths if p]
        if not paths:
            return False
        from ..backend.media import is_subtitle
        subs = [p for p in paths if is_subtitle(p)]
        if subs and self.c.item is not None:
            # sous-titres déposés sur le film (geste de VLC, mpv, Celluloid) : ajoutés à la vidéo en cours —
            # avant, ils REMPLAÇAIENT la file (« Ce fichier ne peut pas être lu », film arrêté)
            for p in subs:
                self.c.player.add_subtitle(p)
            paths = [p for p in paths if not is_subtitle(p)]
            if not paths:
                return True
        self.open_paths(paths)
        return True

    # -- commandes ---------------------------------------------------------------------------
    def toggle_pause(self):
        self.c.toggle()

    def toggle_fullscreen(self):
        if self.pip:                             # de l'image dans l'image au plein écran : d'abord revenir
            self.leave_pip()
            GLib.timeout_add(250, lambda: (self.fullscreen(), False)[1])
            return
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def leave_fullscreen(self):
        """Échap : quitte le plein écran, ou l'image dans l'image."""
        if self.pip:
            self.leave_pip()
        elif self.is_fullscreen():
            self.unfullscreen()

    # -- image dans l'image -----------------------------------------------------------------------
    # Petite fenêtre sans en-tête ni barre, l'image seule, toujours au-dessus des autres et calée en
    # bas à droite de l'écran (X11 : ui/x11.py ; GTK 4 ne sait plus faire ni l'un ni l'autre ; sous
    # Wayland, interdit aux applications). Clic : pause ; molette : volume ; glisser : déplacer ;
    # double-clic, Échap ou le menu : retour à la fenêtre d'avant (taille, file, maximisée).
    PIP_SHARE, PIP_MIN, PIP_MAX = 0.28, 400, 640  # largeur : 28 % de l'écran, entre 400 et 640 px
    PIP_EDGE = 12                                # px près d'un bord : redimensionner (pas de décoration)

    def _pip_width(self) -> int:
        surface = self.get_surface()
        display = self.get_display()
        monitor = display.get_monitor_at_surface(surface) if surface is not None and display else None
        screen = monitor.get_geometry().width if monitor is not None else 1280
        return int(max(self.PIP_MIN, min(self.PIP_MAX, screen * self.PIP_SHARE)))

    def toggle_pip(self):
        if self.pip:
            self.leave_pip()
        else:
            self.enter_pip()

    def enter_pip(self):
        if self.pip or self.c.player.path is None:
            return
        if self.is_fullscreen():                 # d'abord sortir du plein écran, puis réduire
            self.unfullscreen()
            GLib.timeout_add(250, lambda: (self.enter_pip(), False)[1])
            return
        w, h = self.get_default_size()           # taille d'avant une maximisation (pas celle de l'écran)
        self._pip_saved = (w, h, self.is_maximized(), self.split.get_show_sidebar())
        self.pip = True
        self.add_css_class("pip")
        self.split.set_show_sidebar(False)
        if self.is_maximized():
            self.unmaximize()
        self._reveal_bars(False)
        # vraiment cachées : un Gtk.Revealer fermé garde la largeur minimale de son contenu (~530 px pour
        # la barre de commandes), la fenêtre ne descendait pas sous cette largeur
        self.header.set_visible(False)
        self.controls.set_visible(False)
        # sans aucune décoration : ni ombre, ni bordure, ni coins du thème (window.csd), l'image seule
        self.set_decorated(False)
        if settings["pip_width"] >= 160 and settings["pip_height"] >= 90:
            w, h = settings["pip_width"], settings["pip_height"]      # taille retenue (Tof)
        else:
            # première fois : 16:9, quelle que soit l'image (un film en 2.39 a ses bandes noires, comme
            # Firefox ; une fenêtre à la proportion du film était trop plate)
            w = self._pip_width()
            h = int(round(w * 9 / 16))
        self.set_size_request(160, 90)
        self.set_default_size(w, h)
        self._pip_target = (w, h)
        self._pip_frame = (0, 0)
        self.lookup_action("pip").set_state(GLib.Variant("b", True))
        # la fenêtre a pris sa nouvelle taille un peu plus tard : placée et mise au-dessus ensuite
        for ms in (150, 450):
            GLib.timeout_add(ms, self._place_pip)
        self.osd.show("nausicaa-pip-symbolic", tr("Image dans l'image"))

    def _place_pip(self):
        if not self.pip:
            return False
        tw, th = self._pip_target
        dw, dh = tw - self.get_width(), th - self.get_height()
        if self._pip_frame == (0, 0) and (dw or dh) and 0 < dw < 60 and 0 < dh < 60:
            # la taille par défaut compte une marge que la fenêtre n'a pas (10 px chez GTK 4.14 sans
            # décoration) : 400 × 225 donnait 390 × 215, plus du 16:9 — compensée une fois, et gardée
            # pour retenir la taille (sinon la fenêtre rétrécissait à chaque ouverture)
            self._pip_frame = (dw, dh)
            self.set_default_size(tw + dw, th + dh)
        from . import x11
        x11.set_above(self, True)
        area = x11.workarea(self)
        surface = self.get_surface()
        if area is None or surface is None:
            return False
        scale = surface.get_scale_factor() or 1
        ax, ay, aw, ah = area
        margin = 24 * scale
        w, h = self.get_width() * scale, self.get_height() * scale
        x, y = settings["pip_x"], settings["pip_y"]
        # position retenue, si la fenêtre y tient encore entière (autre écran, résolution changée : coin)
        if not (x >= ax and y >= ay and x + w <= ax + aw and y + h <= ay + ah):
            x, y = ax + aw - w - margin, ay + ah - h - margin
        x11.move(self, x, y)
        return False

    def remember_pip(self):
        """Taille et position de l'image dans l'image, pour la prochaine fois (sortie, fermeture)."""
        if not self.pip:
            return
        from . import x11
        settings["pip_width"] = self.get_width()           # taille vue (sans la marge, réajoutée à l'ouverture)
        settings["pip_height"] = self.get_height()
        pos = x11.position(self)
        if pos is not None:
            settings["pip_x"], settings["pip_y"] = pos

    def leave_pip(self):
        if not self.pip:
            return
        from . import x11
        self.remember_pip()
        self.pip = False
        self.remove_css_class("pip")
        self.header.set_visible(True)
        self.controls.set_visible(True)
        self.set_decorated(True)
        self._reveal_pip_overlays(False)
        x11.set_above(self, False)
        self.set_size_request(480, 320)
        w, h, maximized, sidebar = self._pip_saved
        self.set_default_size(w, h)
        if maximized:
            self.maximize()
        self.split.set_show_sidebar(sidebar)
        self.lookup_action("pip").set_state(GLib.Variant("b", False))
        self._reveal_bars(True)
        self._sync_page()
        if not maximized:                        # au centre de l'écran, une fois sa taille reprise (Tof)
            for ms in (150, 450):
                GLib.timeout_add(ms, self._center_window)

    def _center_window(self):
        if self.pip or self.is_maximized() or self.is_fullscreen():
            return False
        from . import x11
        area = x11.workarea(self)
        surface = self.get_surface()
        if area is None or surface is None:
            return False
        scale = surface.get_scale_factor() or 1
        ax, ay, aw, ah = area
        w, h = self.get_width() * scale, self.get_height() * scale
        x11.move(self, ax + (aw - w) // 2, ay + (ah - h) // 2)
        return False

    def seek(self, seconds: float):
        if self.c.player.path is None:
            return
        self.c.seek(seconds)
        target = max(0.0, self.c.player.position() + seconds)
        if self.c.player.duration():
            target = min(target, self.c.player.duration())
        icon = "media-seek-forward-symbolic" if seconds > 0 else "media-seek-backward-symbolic"
        dur = self.c.player.duration()
        self.osd.show(icon, f"{fmt(target)} / {fmt(dur)}" if dur else fmt(target),
                      target / dur if dur else None)

    def change_volume(self, delta: float):
        self.c.change_volume(delta)
        self.show_volume()

    def toggle_mute(self):
        self.c.set_muted(not settings["muted"])
        self.show_volume()

    def show_volume(self):
        vol, muted = settings["volume"], settings["muted"]
        text = tr("Muet") if muted else tr("Volume {0} %").format(int(vol))
        self.osd.show(volume_icon(vol, muted), text, 0 if muted else vol / 100.0, over=vol > 100)

    def frame_step(self, back: bool):
        if self.c.player.path is not None:
            self.c.player.frame_step(back)

    def toggle_subtitles(self):
        visible = not bool(self.c.player.get("sub-visibility", True))
        self.c.player.set_property("sub-visibility", visible)
        self.osd.show("media-view-subtitles-symbolic",
                      tr("Sous-titres affichés") if visible else tr("Sous-titres masqués"))

    def screenshot(self):
        item = self.c.item
        if item is None or self.c.player.path is None:
            return
        folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or os.path.expanduser("~")
        os.makedirs(folder, exist_ok=True)
        stem = item.display_title.replace("/", "-")[:120]
        pos = fmt(self.c.player.position()).replace(":", ".")
        path = os.path.join(folder, f"{stem} {pos}.png")
        n = 2
        while os.path.lexists(path):
            path = os.path.join(folder, f"{stem} {pos} ({n}).png")
            n += 1
        try:
            self.c.player.screenshot(path)
        except SystemError:
            toast(self, tr("Capture impossible"))
            return
        self.osd.show("camera-photo-symbolic", tr("Image enregistrée"))
        toast(self, tr("Image enregistrée : {0}").format(shown(os.path.basename(path))))

    def show_shortcuts(self):
        from .shortcuts import shortcuts_window
        win = shortcuts_window()
        win.set_transient_for(self)
        win.present()

    # -- souris --------------------------------------------------------------------------------
    def _on_click(self, gesture, n_press, _x, _y):
        if self.picture.get_visible_child_name() == "welcome":
            return
        if n_press == 1:
            self._click_toggled = False
            if features.enabled("click_pause") and self.c.player.path is not None:
                self.c.toggle()
                self._click_toggled = True
        elif n_press == 2:
            self._undo_click_pause()             # double-clic : la pause du 1er clic est annulée
            if self.pip:
                self.leave_pip()                 # image dans l'image : double-clic = retour
            else:
                self.toggle_fullscreen()

    def _undo_click_pause(self):
        if self._click_toggled:
            self.c.toggle()
            self._click_toggled = False

    # -- image dans l'image : glisser l'image déplace la fenêtre (elle n'a plus d'en-tête) --------
    def _pip_edge(self, x: float, y: float):
        """Bord ou coin de la fenêtre sous le point (coordonnées de l'image), None au milieu."""
        w, h, m = self.picture.get_width(), self.picture.get_height(), self.PIP_EDGE
        left, right, top, bottom = x < m, x > w - m, y < m, y > h - m
        E = Gdk.SurfaceEdge
        for cond, edge in ((top and left, E.NORTH_WEST), (top and right, E.NORTH_EAST),
                           (bottom and left, E.SOUTH_WEST), (bottom and right, E.SOUTH_EAST),
                           (top, E.NORTH), (bottom, E.SOUTH), (left, E.WEST), (right, E.EAST)):
            if cond:
                return edge
        return None

    _EDGE_CURSORS = {"NORTH_WEST": "nw-resize", "NORTH_EAST": "ne-resize", "SOUTH_WEST": "sw-resize",
                     "SOUTH_EAST": "se-resize", "NORTH": "n-resize", "SOUTH": "s-resize",
                     "WEST": "w-resize", "EAST": "e-resize"}

    def _on_picture_motion(self, _ctl, x, y):
        """Image dans l'image : curseur de redimensionnement près des bords."""
        if not self.pip:
            return
        edge = self._pip_edge(x, y)
        self.picture.set_cursor_from_name(self._EDGE_CURSORS[edge.value_nick.upper().replace("-", "_")]
                                          if edge is not None else None)

    def _on_drag_begin(self, gesture, x, y):
        self._drag_start = (x, y)
        self._dragging_window = False
        if not self.pip:
            return
        edge = self._pip_edge(x, y)
        if edge is None:
            return
        # près d'un bord : redimensionner (la fenêtre n'a plus de bords à elle, décorations coupées)
        self._undo_click_pause()
        self._dragging_window = True
        surface, device = self.get_surface(), gesture.get_device()
        coords = self._to_window(gesture.get_widget(), x, y)
        if surface is None or device is None or coords is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        surface.begin_resize(edge, device, Gdk.BUTTON_PRIMARY, coords[0], coords[1],
                             gesture.get_current_event_time())

    def _to_window(self, widget, x, y):
        """Coordonnées d'un widget vers la fenêtre. PyGObject (Zorin) renvoie (x, y) seuls, sans le
        booléen ; d'autres versions (ok, x, y)."""
        coords = widget.translate_coordinates(self, x, y)
        if coords is None:
            return None
        if len(coords) == 3:
            ok, sx, sy = coords
            return (sx, sy) if ok else None
        return coords

    def _on_drag_update(self, gesture, dx, dy):
        if not self.pip or self._dragging_window or (dx * dx + dy * dy) < 64:
            return
        self._dragging_window = True
        self._undo_click_pause()                 # c'était un glisser, pas un clic
        surface, device = self.get_surface(), gesture.get_device()
        coords = self._to_window(gesture.get_widget(), *self._drag_start)
        if surface is None or device is None or coords is None:      # (glisser simulé : pas de souris)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        surface.begin_move(device, Gdk.BUTTON_PRIMARY, coords[0], coords[1], gesture.get_current_event_time())

    def _on_right_click(self, gesture, _n, x, y):
        menu = Gio.Menu()
        play = Gio.Menu()
        play.append(tr("Pause") if self.c.playing else tr("Lire"), "win.toggle")
        if not self.pip:
            play.append(tr("Quitter le plein écran") if self.is_fullscreen() else tr("Plein écran"),
                        "win.fullscreen")
        play.append(tr("Quitter l'image dans l'image") if self.pip else tr("Image dans l'image"), "win.pip")
        menu.append_section(None, play)
        menu.append_section(None, self._tracks_menu())
        item = self.c.item
        if features.enabled("skip_credits") and item is not None and not item.stream:
            credits = Gio.Menu()
            credits.append(tr("Le générique commence ici"), "win.credits-here")
            if self.credits_store.get(item.path) is not None:
                credits.append(tr("Oublier le générique de cette série"), "win.credits-forget")
            menu.append_section(None, credits)
        more = Gio.Menu()
        more.append(tr("Ouvrir des vidéos…"), "win.open")
        more.append(tr("Capture de l'image"), "win.screenshot")
        more.append(tr("Afficher la file de lecture"), "win.playlist")
        menu.append_section(None, more)
        popup_menu(self.picture, menu, x, y)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_wheel(self, _ctl, _dx, dy):
        if not features.enabled("wheel_volume"):
            return False
        self._wheel_acc -= dy                     # vers le haut (dy < 0) : plus fort
        changed = False
        while abs(self._wheel_acc) >= 1:
            step = 1 if self._wheel_acc > 0 else -1
            self._wheel_acc -= step
            self.c.change_volume(step * settings["wheel_step"])
            changed = True
        if changed:
            self.show_volume()
        return True

    def _on_motion(self, _ctl, x, y):
        if (x, y) == self._last_motion:            # GTK renvoie un mouvement quand une barre apparaît
            return
        self._last_motion = (x, y)
        self._reveal_bars(True)
        self._schedule_hide()

    # -- barres en plein écran -------------------------------------------------------------
    def _reveal_bars(self, reveal: bool):
        if self.pip:                             # image dans l'image : l'image seule, commandes au survol
            self.toolbar.set_reveal_top_bars(False)
            self.toolbar.set_reveal_bottom_bars(False)
            self.float_revealer.set_reveal_child(False)
            self._reveal_pip_overlays(reveal)
            return                               # (curseur : celui des bords, _on_picture_motion)
        self._reveal_pip_overlays(False)
        fullscreen = self.is_fullscreen()
        self.toolbar.set_reveal_top_bars(reveal or not fullscreen)
        show = (reveal or not fullscreen) and self.picture.get_visible_child_name() != "welcome"
        self.toolbar.set_reveal_bottom_bars(show)
        self.float_revealer.set_reveal_child(show and fullscreen)
        self.picture.set_cursor_from_name(None if reveal or not fullscreen else "none")

    def _schedule_hide(self):
        if self._hide_timer:
            GLib.source_remove(self._hide_timer)
        self._hide_timer = GLib.timeout_add(HIDE_DELAY, self._hide_bars)

    def _menu_open(self) -> bool:
        """Un menu est ouvert : les barres restent. Le menu principal compris (en plein écran, il se
        refermait tout seul au bout de 2 s : ouvrir son popover fait « quitter » l'en-tête au pointeur), et
        le menu du clic droit sur l'image."""
        if any(b.get_active() for b in (self.controls.volume, self.controls.tracks, self.main_menu_button)):
            return True
        pop = getattr(self.picture, "_nausicaa_popover", None)
        return pop is not None and pop.get_visible()

    def _on_pointer_leave(self, *_):
        """La souris quitte la fenêtre : en image dans l'image, les commandes s'effacent tout de suite."""
        if self.pip:
            self._over_bars = False
            self._reveal_pip_overlays(False)

    def _hide_bars(self):
        self._hide_timer = 0
        if self.pip:                             # souris immobile : commandes effacées (pas sur elles)
            if self._over_bars:
                self._schedule_hide()
            else:
                self._reveal_pip_overlays(False)
            return False
        if not self.is_fullscreen():
            return False
        if self._over_bars or self._menu_open() or not self.c.playing:
            self._schedule_hide()
            return False
        self._reveal_bars(False)
        return False

    def _float_controls(self, floating: bool):
        """Plein écran : commandes compactes, centrées en bas, arrondies, posées sur l'image (comme
        Celluloid / VLC). Couleurs du thème, comme en fenêtré : fond de fenêtre (celui des Propriétés
        d'Odysseus, transparence du thème comprise), barre d'avance en couleur d'accent — demande de
        Tof ; la classe « osd » de libadwaita imposait du noir et une barre blanche. La barre quitte alors l'Adw.ToolbarView (sa zone du bas rognait les coins arrondis et l'ombre) pour un
        Gtk.Revealer posé sur l'image. Fenêtré : barre ordinaire sur toute la largeur."""
        c = self.controls
        if floating and c.get_parent() is not None and self.float_revealer.get_child() is not c:
            self.toolbar.remove(c)
            self.float_revealer.set_child(c)
        elif not floating and self.float_revealer.get_child() is c:
            self.float_revealer.set_child(None)
            self.toolbar.add_bottom_bar(c)
        if floating:
            c.add_css_class("floating-controls")
        else:
            c.remove_css_class("floating-controls")
        if floating:
            surface = self.get_surface()
            monitor = self.get_display().get_monitor_at_surface(surface) if surface is not None else None
            width = monitor.get_geometry().width if monitor is not None else self.get_width()
            c.set_size_request(max(480, min(820, int(width * 0.6))), -1)
        else:
            c.set_size_request(-1, -1)

    def controls_visible(self) -> bool:
        if self.float_revealer.get_child() is self.controls:
            return self.float_revealer.get_reveal_child()
        return self.toolbar.get_reveal_bottom_bars()

    def _on_fullscreen(self, *_):
        full = self.is_fullscreen()
        self.toolbar.set_extend_content_to_top_edge(full)
        self.toolbar.set_extend_content_to_bottom_edge(full)
        self.controls.fullscreen.set_icon_name("view-restore-symbolic" if full else "view-fullscreen-symbolic")
        self.controls.fullscreen.set_tooltip_text(tr("Quitter le plein écran") if full else tr("Plein écran"))
        self._float_controls(full)
        # plein écran : l'en-tête est posé sur l'image, l'encart du volume passe en dessous
        self.osd.set_margin_top(84 if full else 24)
        if getattr(self.app, "mpris", None) is not None:
            self.app.mpris.fullscreen_changed()
        if full:
            self.add_css_class("fullscreen-video")
            self.split.set_show_sidebar(False)
            self._reveal_bars(True)
            self._schedule_hide()
        else:
            self.remove_css_class("fullscreen-video")
            self._reveal_bars(True)
            self.split.set_show_sidebar(settings["playlist_visible"])

    # -- fenêtre réduite : pause, puis reprise ----------------------------------------------
    def _on_realize(self, *_):
        surface = self.get_surface()
        if surface is not None:
            self._surface_handler = surface.connect("notify::state", self._on_surface_state)

    def _on_unrealize(self, *_):
        surface = self.get_surface()
        if surface is not None and self._surface_handler:
            surface.disconnect(self._surface_handler)
        self._surface_handler = None

    @staticmethod
    def _hidden_flags():
        """X11 : MINIMIZED (état _NET_WM_STATE_HIDDEN). Wayland n'annonce pas la réduction à
        l'application : SUSPENDED (GTK 4.12+, fenêtre plus du tout visible) en tient lieu."""
        flags = Gdk.ToplevelState.MINIMIZED
        if not type(Gdk.Display.get_default()).__name__.startswith("X11"):
            flags |= getattr(Gdk.ToplevelState, "SUSPENDED", 0)
        return flags

    def _on_surface_state(self, surface, *_):
        hidden = bool(surface.get_state() & self._hidden_flags())
        self.set_minimized_state(hidden)

    def set_minimized_state(self, hidden: bool):
        if hidden:
            if features.enabled("minimize_pause") and self.c.playing:
                self.c.set_paused(True)
                self._paused_by_minimize = True
        elif self._paused_by_minimize:
            self._paused_by_minimize = False
            if self.c.player.path is not None and self.c.player.paused:
                self.c.set_paused(False)

    # -- pistes ------------------------------------------------------------------------------
    @staticmethod
    def _track_label(t: dict) -> str:
        parts = []
        if t.get("title"):
            parts.append(t["title"])
        if t.get("lang"):
            parts.append(t["lang"])
        if t.get("codec") and not parts:
            parts.append(str(t["codec"]))
        if t.get("external"):
            parts.append(tr("fichier"))
        label = " — ".join(str(p) for p in parts) or tr("Piste {0}").format(t.get("id"))
        # entrées de Gio.Menu : « _ » = lettre soulignée (« eng_SDH » devenait « engSDH »)
        return shown(label).replace("_", "__")

    def _tracks_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        audio = Gio.Menu()
        for t in self.c.player.tracks("audio"):
            audio.append(self._track_label(t), f"win.audio-track::{t['id']}")
        if audio.get_n_items() > 1:
            menu.append_submenu(tr("Piste audio"), audio)
        subs = Gio.Menu()
        subs.append(tr("Aucun"), "win.subtitle-track::no")
        for t in self.c.player.tracks("sub"):
            subs.append(self._track_label(t), f"win.subtitle-track::{t['id']}")
        add = Gio.Menu()
        add.append(tr("Ajouter des sous-titres…"), "win.add-subtitles")
        subs.append_section(None, add)
        menu.append_submenu(tr("Sous-titres"), subs)
        speed = Gio.Menu()
        for s in SPEEDS:
            speed.append(speed_text(float(s)) if s != "1.0" else tr("Normale"), f"win.speed::{s}")
        menu.append_submenu(tr("Vitesse"), speed)
        return menu

    def _sync_tracks(self):
        self.controls.tracks.set_menu_model(self._tracks_menu())
        sel = {"audio": "", "sub": "no"}
        for kind in ("audio", "sub"):
            for t in self.c.player.tracks(kind):
                if t.get("selected"):
                    sel[kind] = str(t["id"])
        self.lookup_action("audio-track").set_state(GLib.Variant("s", sel["audio"]))
        self.lookup_action("subtitle-track").set_state(GLib.Variant("s", sel["sub"]))

    def _on_audio_track(self, action, value):
        action.set_state(value)
        self.c.player.select_track("aid", value.get_string())

    def _on_subtitle_track(self, action, value):
        action.set_state(value)
        self.c.player.select_track("sid", value.get_string())

    def _on_speed(self, action, value):
        action.set_state(value)
        speed = float(value.get_string())
        self.c.player.set_property("speed", speed)
        self.osd.show("preferences-system-time-symbolic",
                      tr("Vitesse normale") if speed == 1 else tr("Vitesse {0}").format(speed_text(speed)))

    # -- événements du lecteur -------------------------------------------------------------
    def _on_event(self, event, *args):
        if event == "position":
            self.controls.set_position(args[0])
            self._sync_credits(args[0])
        elif event == "duration":
            self.controls.set_duration(args[0])
            self._find_credits()
        elif event == "chapters":
            self._find_credits()
        elif event == "state":
            self._sync_state()
        elif event in ("item", "title"):
            self._sync_item()
            if event == "item":
                self._credits_loaded = False
                self._find_credits()
        elif event == "loaded":
            self._credits_loaded = True
            self._find_credits()
        elif event == "video":
            self._sync_page()
            self._auto_fullscreen()
        elif event == "tracks":
            self._sync_tracks()
            self._auto_fullscreen()
        elif event == "speed":                       # changée ailleurs (MPRIS Rate) : la coche du menu suit
            speed = float(self.c.player.get("speed") or 1.0)
            text = next((s for s in SPEEDS if abs(float(s) - speed) < 1e-6), None)
            action = self.lookup_action("speed")
            if action is not None and text is not None and action.get_state().get_string() != text:
                action.set_state(GLib.Variant("s", text))
            self._sync_page()
        elif event == "volume":
            self.controls.sync_volume()
        elif event == "queue":
            self._sync_buttons()
            self._sync_page()
            self._sync_credits(self.c.player.position())
        elif event == "resumed":
            toast(self, tr("Reprise à {0}").format(fmt(args[0])), tr("Depuis le début"), self.c.restart, timeout=6)
        elif event == "error":
            item, message = args
            name = shown(item.display_title) if item is not None else ""
            toast(self, tr("« {0} » : {1}").format(name, message) if name else message)
        elif event == "ended":
            self._sync_state()

    def _sync_all(self):
        self._sync_item()
        self._sync_state()
        self._sync_page()
        self._sync_buttons()
        self.controls.sync_volume()
        self.controls.set_duration(self.c.player.duration())
        self._sync_resume_button()
        self._find_credits()

    def _sync_page(self):
        if self.c.player.path is None:
            name = "video" if self._opening else "welcome"
        elif self.c.player.get("vid") in (False, None) and self.c.player.tracks("audio") \
                and not self.c.player.tracks("video"):
            name = "audio"
        else:
            name = "video"
        self.picture.set_visible_child_name(name)
        if name == "welcome":
            self._sync_resume_button()
            self._sync_welcome()
        # accueil : pas de commandes de lecture (il n'y a rien à lire)
        if self.pip:
            pass                                 # image dans l'image : aucune barre
        elif not self.is_fullscreen():
            self.toolbar.set_reveal_bottom_bars(name != "welcome")
        else:
            self.float_revealer.set_reveal_child(name != "welcome" and self.float_revealer.get_reveal_child())

    def _sync_resume_button(self):
        data = self.c.saved_queue(self.c.queue_file) if self.c.player.path is None else None
        if data and data.get("current"):
            from ..backend.media import Item
            title = Item(data["current"], data.get("titles", {}).get(data["current"], "")).display_title
            n = len(data["songs"])
            label = tr("Reprendre « {0} »").format(shown(title))
            if n > 1:
                label += " " + ntr("(+ {0} autre)", "(+ {0} autres)", n - 1).format(n - 1)
            for b in self._resume_buttons():
                b.set_label(label)
                b.get_child().set_ellipsize(3)
                b.get_child().set_max_width_chars(48 if b is self.resume_button else 32)
                b.set_visible(True)
        else:
            for b in self._resume_buttons():
                b.set_visible(False)

    def _resume_buttons(self) -> list:
        return [self.resume_button] + ([self.library.resume_button] if self.library is not None else [])

    def _sync_item(self):
        item = self.c.item
        loaded = self.c.player.path is not None
        title = APP_NAME
        if item is not None and loaded:
            media_title = self.c.player.get("media-title") or ""
            # flux : le titre que donne mpv ; fichier local : son titre enregistré s'il a du sens
            # (media.tag_title écarte les signatures de rippeurs), sinon son nom
            title = shown(item.title or (media_title if item.stream and media_title else item.display_title))
        self.title_widget.set_title(title)
        self.set_title(title if title == APP_NAME else f"{title} — {APP_NAME}")
        n = len(self.c.queue)
        self.title_widget.set_subtitle(f"{self.c.queue.pos + 1} / {n}" if n > 1 and self.c.queue.pos >= 0 else "")
        if item is not None:
            self.audio_page.set_title(shown(item.display_title))
        self._sync_buttons()
        self._sync_page()

    def _sync_buttons(self):
        q = self.c.queue
        has = self.c.item is not None
        self.lookup_action("next").set_enabled(has and q.next_index() is not None and len(q) > 1)
        self.lookup_action("previous").set_enabled(has)
        for name in ("screenshot", "add-subtitles", "subtitles-visible"):
            self.lookup_action(name).set_enabled(has)
        self.lookup_action("save-playlist").set_enabled(len(q) > 0)
        self.lookup_action("clear-queue").set_enabled(len(q) > 0)

    def _sync_state(self):
        playing = self.c.playing
        self.controls.set_playing(playing)
        self.pip_play.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.pip_play.set_tooltip_text(tr("Pause") if playing else tr("Lire"))
        self.app.sync_inhibit(playing)
        if self.is_fullscreen():
            if playing:
                self._schedule_hide()
            else:
                self._reveal_bars(True)
        self._sync_page()

    def _on_sidebar(self, *_):
        shown_now = self.split.get_show_sidebar()
        self.playlist_action.set_state(GLib.Variant("b", shown_now))
        self.controls.playlist.set_active(shown_now)
        if not self.is_fullscreen() and not self.pip:
            settings["playlist_visible"] = shown_now

    @staticmethod
    def _playlist_side() -> Gtk.PackType:
        """File à droite (par défaut) ou à gauche (réglage playlist_side, idée de Tof : gauchers / droitiers).
        Les boutons de la fenêtre suivent d'eux-mêmes : libadwaita les met dans l'en-tête du bord droit."""
        return Gtk.PackType.START if settings["playlist_side"] == "start" else Gtk.PackType.END

    def _on_setting(self, key, _value):
        if key in ("volume", "muted", "volume_max"):
            self.controls.sync_volume()
        elif key == "playlist_side":
            self.split.set_sidebar_position(self._playlist_side())       # tout de suite, sans redémarrer
        elif key == "features":
            self._sync_welcome()                                          # bibliothèque activée / coupée
            self._sync_tmdb()
            self._find_credits()                                          # « Passer le générique »
        elif key == "tmdb_key":
            from ..backend import tmdb
            tmdb.forget_cache()                                           # « pas trouvé » d'une autre clé
            self._sync_tmdb(force=True)
        elif key in ("library_dirs", "library_images") and self.library is not None:
            self.library.refresh(force=True)

    # -- fermeture -----------------------------------------------------------------------------
    def _on_close_request(self, *_):
        if self.pip:                             # fermée en image dans l'image : la taille d'avant
            self.remember_pip()
            settings["window_width"], settings["window_height"] = self._pip_saved[:2]
        else:
            # taille d'avant la maximisation / le plein écran (GTK 4) ; get_width() perdait quelques
            # pixels à chaque ouverture selon le gestionnaire de fenêtres
            settings["window_width"], settings["window_height"] = self.get_default_size()
        if self.get_visible():
            settings["window_maximized"] = self._pip_saved[2] if self.pip else self.is_maximized()
        return False

    def teardown(self):
        """Avant la destruction : plus d'écouteurs (le lecteur peut encore prévenir)."""
        self.c.disconnect(self._on_event)
        self.c.disconnect(self.panel._on_event)
        settings.disconnect(self._on_setting)
        self.osd.dispose_timer()
        if self._hide_timer:
            GLib.source_remove(self._hide_timer)
            self._hide_timer = 0

