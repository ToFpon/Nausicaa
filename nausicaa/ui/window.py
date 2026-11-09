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
    lu »). La vidéo reste donc toujours en place, dessous."""

    def __init__(self, video: Gtk.Widget, pages: dict):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("video-area")
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


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, icon_name=APP_ID)
        self.add_css_class("nausicaa-window")
        self.app = app
        self.c = app.controller
        self.set_default_size(settings["window_width"], settings["window_height"])
        self.set_size_request(480, 320)
        self._click_toggled = False
        self._wheel_acc = 0.0
        self._hide_timer = 0
        self._over_bars = False
        self._paused_by_minimize = False
        self._surface_handler = None
        self._last_motion = (0.0, 0.0)

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

        # -- barres ----------------------------------------------------------------------
        self.header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title=APP_NAME)
        self.header.set_title_widget(self.title_widget)
        open_button = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=tr("Ouvrir des vidéos"),
                                 action_name="win.open")
        self.header.pack_start(open_button)
        self.header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self._main_menu(),
                                            tooltip_text=tr("Menu principal"), primary=True))
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
        self.split = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END, show_sidebar=settings["playlist_visible"],
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
        self.c.connect(self._on_event)
        settings.connect(self._on_setting)
        self.connect("notify::fullscreened", self._on_fullscreen)
        self.connect("close-request", self._on_close_request)
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
        self._sync_all()

    # -- construction -----------------------------------------------------------------------
    def _build_welcome(self) -> Gtk.Widget:
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
            ("restore-queue", lambda: self.video.when_ready(c.restore_saved), []),
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
            ("add-subtitles", self.add_subtitles_dialog, []),
            ("save-playlist", self.panel.save_queue, ["<Control>s"]),
            ("clear-queue", c.clear, []),
            ("show-help-overlay", self.show_shortcuts, ["<Control>question", "F1"]),
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
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", self._on_right_click)
        self.picture.add_controller(right)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self._on_wheel)
        self.picture_overlay.add_controller(wheel)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.toolbar.add_controller(motion)

    def _install_drop(self):
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("enter", lambda *_: (self.drop_hint.set_visible(True), Gdk.DragAction.COPY)[1])
        target.connect("leave", lambda *_: self.drop_hint.set_visible(False))
        target.connect("drop", self._on_drop)
        self.picture_overlay.add_controller(target)

    # -- ouverture --------------------------------------------------------------------------
    def open_paths(self, paths: list[str], replace: bool = True):
        def done(n):
            if not n:
                toast(self, tr("Aucune vidéo trouvée"))
        self.video.when_ready(lambda: self.c.open_paths(paths, replace=replace, on_done=done))

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
        self.open_paths(paths)
        return True

    # -- commandes ---------------------------------------------------------------------------
    def toggle_pause(self):
        self.c.toggle()

    def toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def leave_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()

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
            if self._click_toggled:              # double-clic : la pause du 1er clic est annulée
                self.c.toggle()
                self._click_toggled = False
            self.toggle_fullscreen()

    def _on_right_click(self, gesture, _n, x, y):
        menu = Gio.Menu()
        play = Gio.Menu()
        play.append(tr("Pause") if self.c.playing else tr("Lire"), "win.toggle")
        play.append(tr("Quitter le plein écran") if self.is_fullscreen() else tr("Plein écran"), "win.fullscreen")
        menu.append_section(None, play)
        menu.append_section(None, self._tracks_menu())
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
        return any(b.get_active() for b in (self.controls.volume, self.controls.tracks))

    def _hide_bars(self):
        self._hide_timer = 0
        if not self.is_fullscreen():
            return False
        if self._over_bars or self._menu_open() or not self.c.playing:
            self._schedule_hide()
            return False
        self._reveal_bars(False)
        return False

    def _float_controls(self, floating: bool):
        """Plein écran : commandes compactes, centrées en bas, arrondies, posées sur l'image (comme
        Celluloid / VLC) — classe « osd » du thème : fond sombre translucide, icônes claires. La barre
        quitte alors l'Adw.ToolbarView (sa zone du bas rognait les coins arrondis et l'ombre) pour un
        Gtk.Revealer posé sur l'image. Fenêtré : barre ordinaire sur toute la largeur."""
        c = self.controls
        if floating and c.get_parent() is not None and self.float_revealer.get_child() is not c:
            self.toolbar.remove(c)
            self.float_revealer.set_child(c)
        elif not floating and self.float_revealer.get_child() is c:
            self.float_revealer.set_child(None)
            self.toolbar.add_bottom_bar(c)
        for cls in ("osd", "floating-controls"):
            if floating:
                c.add_css_class(cls)
            else:
                c.remove_css_class(cls)
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
        return shown(label)

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
        elif event == "duration":
            self.controls.set_duration(args[0])
        elif event == "state":
            self._sync_state()
        elif event in ("item", "title"):
            self._sync_item()
        elif event == "video":
            self._sync_page()
        elif event == "tracks":
            self._sync_tracks()
            self._sync_page()
        elif event == "volume":
            self.controls.sync_volume()
        elif event == "queue":
            self._sync_buttons()
            self._sync_page()
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

    def _sync_page(self):
        if self.c.player.path is None:
            name = "welcome"
        elif self.c.player.get("vid") in (False, None) and self.c.player.tracks("audio") \
                and not self.c.player.tracks("video"):
            name = "audio"
        else:
            name = "video"
        if name == "welcome":
            self._sync_resume_button()
        self.picture.set_visible_child_name(name)
        # accueil : pas de commandes de lecture (il n'y a rien à lire)
        if not self.is_fullscreen():
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
            self.resume_button.set_label(label)
            self.resume_button.get_child().set_ellipsize(3)
            self.resume_button.get_child().set_max_width_chars(48)
            self.resume_button.set_visible(True)
        else:
            self.resume_button.set_visible(False)

    def _sync_item(self):
        item = self.c.item
        loaded = self.c.player.path is not None
        title = APP_NAME
        if item is not None and loaded:
            media_title = self.c.player.get("media-title") or ""
            # le titre donné par le fichier (étiquette) sert pour les flux ; pour un fichier local,
            # son nom : c'est lui que l'on reconnaît (« Épisode 3.mkv » plutôt qu'un titre de rippeur)
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
        if not self.is_fullscreen():
            settings["playlist_visible"] = shown_now

    def _on_setting(self, key, _value):
        if key in ("volume", "muted", "volume_max"):
            self.controls.sync_volume()

    # -- fermeture -----------------------------------------------------------------------------
    def _on_close_request(self, *_):
        if not self.is_fullscreen() and not self.is_maximized():
            settings["window_width"] = self.get_width()
            settings["window_height"] = self.get_height()
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

