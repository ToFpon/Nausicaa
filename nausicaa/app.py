"""Point d'entrée de l'application Nausicaa."""

from __future__ import annotations

import os
import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, VERSION  # noqa: E402
from .i18n import tr  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class NausicaaApp(Adw.Application):
    """Instance unique : « nausicaa vidéo.mkv » lancé une seconde fois l'envoie à la première
    (la file est remplacée, comme un double-clic dans le gestionnaire de fichiers)."""

    def __init__(self, queue_file: str = ""):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.HANDLES_OPEN)
        GLib.set_application_name(APP_NAME)
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             tr("Afficher la version"), None)
        self.add_main_option("enqueue", ord("e"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             tr("Ajouter à la file au lieu de la remplacer"), None)
        self._queue_file = queue_file
        self.controller = None
        self.window = None
        self._inhibit = 0

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(DATA_DIR, "base.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        icons = os.path.join(DATA_DIR, "icons")
        if os.path.isdir(icons):
            Gtk.IconTheme.get_for_display(display).add_search_path(icons)
        from .ui.preferences import apply_theme
        apply_theme()
        from .backend.controller import Controller
        self.controller = Controller(queue_file=self._queue_file)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (self._quit(), False)[1])
        for name, cb, accels in (("preferences", self._prefs, ["<Control>comma"]),
                                 ("about", self._about, []),
                                 ("quit", self._quit, ["<Control>q"])):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        self.mpris = None
        self._sync_mpris()
        from .settings import settings
        settings.connect(self._on_setting)

    # -- MPRIS : panneau de GNOME, touches multimédia, casque Bluetooth --------------------------
    def _sync_mpris(self):
        from . import features
        on = features.enabled("mpris") and self.get_dbus_connection() is not None
        if on and self.mpris is None:
            from .backend.mpris import Mpris
            self.mpris = Mpris(self.controller, self.present, self._quit, self._mpris_fullscreen)
            self.mpris.start()
        elif not on and self.mpris is not None:
            self.mpris.stop()
            self.mpris = None

    def _mpris_fullscreen(self, value):
        """value None : lire l'état ; sinon passer en plein écran ou en sortir."""
        win = self.window
        if value is None:
            return win is not None and win.is_fullscreen()
        if win is not None:
            GLib.idle_add(lambda: ((win.fullscreen() if value else win.unfullscreen()), False)[1])
        return value

    def _on_setting(self, key, _value):
        if key == "features":
            self._sync_mpris()

    def do_shutdown(self):
        if self.mpris is not None:
            self.mpris.stop()
            self.mpris = None
        self._close_window()
        if self.controller is not None:
            self.controller.shutdown()          # contexte de rendu déjà libéré : mpv peut s'arrêter
        Adw.Application.do_shutdown(self)

    # -- fenêtre --------------------------------------------------------------------------------
    def _main_window(self):
        if self.window is None:
            from .ui.window import MainWindow
            self.window = MainWindow(self)
            self.window.connect("close-request", self._on_close_request)
        return self.window

    def _on_close_request(self, win):
        # une seule fenêtre : la fermer, c'est quitter (le lecteur s'arrête avec elle)
        GLib.idle_add(lambda: (self._quit(), False)[1])
        return False

    def present(self):
        win = self._main_window()
        win.present()

    def do_activate(self):
        self.present()

    def do_open(self, files, n, hint):
        paths = [f.get_path() or f.get_uri() for f in files]
        self.present()
        if paths:
            self.window.open_paths([p for p in paths if p])

    def do_command_line(self, cmd):
        opts = cmd.get_options_dict().end().unpack()
        if opts.get("version"):
            cmd.print_literal(f"{APP_NAME} {VERSION}\n")
            return 0
        cwd = cmd.get_cwd() or os.path.expanduser("~")
        paths = []
        for a in cmd.get_arguments()[1:]:
            f = Gio.File.new_for_commandline_arg_and_cwd(a, cwd)
            paths.append(f.get_path() or f.get_uri())
        self.present()
        if paths:
            self.window.open_paths(paths, replace=not opts.get("enqueue"))
        return 0

    def _close_window(self):
        win, self.window = self.window, None
        if win is not None:
            win.teardown()
            if self.controller is not None:
                self.controller.stop()          # avant la fenêtre : sinon mpv voit sa sortie vidéo disparaître
            # la fenêtre (et son contexte de rendu de mpv) part AVANT l'arrêt de mpv, sinon mpv avorte
            win.destroy()
            ctx = GLib.MainContext.default()
            while ctx.pending():
                ctx.iteration(False)

    def _quit(self):
        if self.window is not None and not self.window.is_fullscreen() and not self.window.is_maximized():
            from .settings import settings
            settings["window_width"] = self.window.get_width()
            settings["window_height"] = self.window.get_height()
        self.sync_inhibit(False)
        self._close_window()
        self.quit()

    # -- veille ---------------------------------------------------------------------------------
    def sync_inhibit(self, playing: bool):
        """Pendant la lecture : ni économiseur d'écran, ni mise en veille."""
        if playing and not self._inhibit and self.window is not None:
            self._inhibit = self.inhibit(self.window, Gtk.ApplicationInhibitFlags.IDLE
                                         | Gtk.ApplicationInhibitFlags.SUSPEND, tr("Lecture d'une vidéo"))
        elif not playing and self._inhibit:
            self.uninhibit(self._inhibit)
            self._inhibit = 0

    # -- fenêtres annexes ------------------------------------------------------------------------
    def _prefs(self):
        from .ui.preferences import PreferencesWindow
        d = PreferencesWindow()
        d.set_transient_for(self.window)
        d.present()

    def _about(self):
        about = Adw.AboutWindow(transient_for=self.window, modal=True,
                                application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
                                developer_name="Tof",
                                comments=tr("Un petit lecteur vidéo pour GNOME, qui lit vos vidéos et rien "
                                            "d'autre."),
                                website="https://github.com/ToFpon/Nausicaa",
                                issue_url="https://github.com/ToFpon/Nausicaa/issues",
                                license_type=Gtk.License.GPL_3_0)
        about.add_credit_section(tr("Inspiré de"), ["Celluloid https://celluloid-player.github.io", "VLC"])
        about.add_acknowledgement_section(tr("Moteurs"), ["mpv https://mpv.io", "python-mpv"])
        about.present()


def _process_name(name: str):
    """Nom du processus pour le noyau : « nausicaa » dans le Moniteur système, ps, killall."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(15, ctypes.c_char_p(name.encode()[:15]), 0, 0, 0)     # 15 = PR_SET_NAME
    except (OSError, AttributeError):
        pass


def main(argv=None) -> int:
    _process_name("nausicaa")
    import faulthandler
    faulthandler.enable(all_threads=True)
    GLib.set_prgname(APP_ID)
    app = NausicaaApp()
    return app.run(argv if argv is not None else sys.argv)
