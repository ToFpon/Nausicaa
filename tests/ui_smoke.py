#!/usr/bin/env python3
"""Test de fumée de l'interface : pilote une vraie fenêtre (sous xvfb-run, vrai mpv, vraie image
OpenGL) et signale toute exception.

    xvfb-run -a python3.12 tests/ui_smoke.py        # doit finir par « Aucune erreur. »
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
TMP = tempfile.mkdtemp(prefix="nausicaa-ui-")
os.environ["HOME"] = os.path.join(TMP, "home")
for var, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[var] = os.path.join(TMP, "home", sub)
os.environ["NAUSICAA_AO"] = "null"
os.makedirs(os.environ["HOME"])

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

errors = []


def hook(t, v, tb):
    errors.append("".join(traceback.format_exception(t, v, tb)))
    sys.__excepthook__(t, v, tb)


sys.excepthook = hook

import media  # noqa: E402
from nausicaa import features  # noqa: E402
from nausicaa.app import NausicaaApp  # noqa: E402
from nausicaa.i18n import tr  # noqa: E402
from nausicaa.settings import settings  # noqa: E402

LIB = media.library(os.path.join(TMP, "lib"))

# Un gestionnaire de fenêtres : sans lui, ni plein écran ni réduction (X11 les demande au WM)
WM = None
if os.environ.get("DISPLAY") and shutil.which("openbox"):
    import subprocess
    WM = subprocess.Popen(["openbox", "--sm-disable"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
app = NausicaaApp()
steps = []
log = []


def step(fn):
    steps.append(fn)
    return fn


class Wait:
    """Les étapes sont des générateurs : « yield Wait(s) » rend la main à la vraie boucle GLib."""
    def __init__(self, seconds=0.4, until=None):
        self.seconds = seconds
        self.until = until


def pump(seconds=0.4):
    return Wait(seconds)


CRITICALS = []
_err_r, _err_w = os.pipe()
_err_orig = os.dup(2)
os.dup2(_err_w, 2)


def _tee_stderr():
    with os.fdopen(_err_r, "rb", 0) as f:
        for line in iter(f.readline, b""):
            os.write(_err_orig, line)
            if b"-CRITICAL **" in line:
                CRITICALS.append(line.decode("utf-8", "replace").strip())


threading.Thread(target=_tee_stderr, daemon=True).start()


def check(cond, msg):
    if not cond:
        errors.append("ÉCHEC : " + msg)
        print("✗", msg)
    else:
        print("✓", msg)


def run_all(win):
    def driver():
        for fn in steps:
            log.append(fn.__name__)
            gen = fn(win)
            if gen is not None and hasattr(gen, "__next__"):
                yield from gen
            yield Wait(0.2)

    it = driver()

    def tick():
        try:
            w = next(it)
        except StopIteration:
            app._quit()
            return False
        except Exception:
            errors.append(traceback.format_exc())
            app._quit()
            return False
        start = time.time()

        def poll():
            if w.until is not None:
                if w.until() or time.time() - start > w.seconds:
                    GLib.timeout_add(50, tick)
                    return False
                return True
            GLib.timeout_add(50, tick)
            return False
        if w.until is not None:
            GLib.timeout_add(50, poll)
        else:
            GLib.timeout_add(int(w.seconds * 1000), poll)
        return False
    tick()
    return False


def c():
    return app.controller


def basename():
    return os.path.basename(c().player.path or "")


def frame_has_colour(win) -> bool:
    """L'image de la vidéo est vraiment dessinée (pas noire) : capture du widget."""
    view = win.video
    p = Gtk.WidgetPaintable.new(view)
    snap = Gtk.Snapshot()
    p.snapshot(snap, view.get_width(), view.get_height())
    node = snap.to_node()
    if node is None:
        return False
    tex = win.get_renderer().render_texture(node, None)
    w, h = tex.get_width(), tex.get_height()
    data = tex.save_to_png_bytes().get_data()
    from gi.repository import GdkPixbuf
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(data)
    loader.close()
    pix = loader.get_pixbuf()
    px = pix.get_pixels()
    stride, n = pix.get_rowstride(), pix.get_n_channels()
    bright = 0
    for x, y in ((w // 4, h // 2), (w // 2, h // 3), (3 * w // 4, h // 2), (w // 3, h // 4)):
        o = y * stride + x * n
        if max(px[o], px[o + 1], px[o + 2]) > 60:
            bright += 1
    if bright < 2:
        print("  (image : %dx%d, échantillons clairs : %d)" % (w, h, bright))
    return bright >= 2


@step
def accueil(win):
    yield pump(0.3)
    check(win.picture.get_visible_child_name() == "welcome", "page d'accueil au démarrage")
    check(not win.controls_visible(), "accueil : pas de commandes de lecture")
    check(not win.resume_button.get_visible(), "aucune file à reprendre au premier lancement")


@step
def ouvrir_un_episode(win):
    win.open_paths([LIB["e2"]])
    yield Wait(10, until=lambda: basename() == "Épisode 2.mp4" and c().player.position() > 0.3)
    check(basename() == "Épisode 2.mp4", "l'épisode ouvert est lu")
    check([os.path.basename(i.path) for i in c().queue.items] == ["Épisode 1.mp4", "Épisode 2.mp4", "Épisode 10.mp4"],
          "épisodes suivants : tout le dossier, ordre naturel")
    check(win.picture.get_visible_child_name() == "video", "page vidéo")
    check(win.controls_visible(), "commandes affichées")
    check(win.title_widget.get_title() == "Épisode 2" and win.title_widget.get_subtitle() == "2 / 3",
          "titre : nom de la vidéo et place dans la file")
    yield Wait(3, until=lambda: frame_has_colour(win))
    check(frame_has_colour(win), "image de la vidéo dessinée (mpv → Gtk.GLArea)")
    yield Wait(3, until=lambda: len(c().player.tracks("audio")) == 2)
    labels = []
    menu = win.controls.tracks.get_menu_model()
    for i in range(menu.get_n_items()):
        labels.append(menu.get_item_attribute_value(i, "label").get_string())
    check(tr("Piste audio") in labels and tr("Sous-titres") in labels and tr("Vitesse") in labels,
          "menu des pistes : audio (2 pistes), sous-titres, vitesse")
    check(win.lookup_action("subtitle-track").get_state().get_string() not in ("", "no"),
          "sous-titres posés à côté de la vidéo trouvés et affichés")


@step
def clic_pause_double_clic(win):
    playing = c().playing
    check(playing, "lecture en cours")
    win._on_click(None, 1, 10, 10)
    yield pump(0.3)
    check(not c().playing, "clic : pause immédiate")
    win._on_click(None, 1, 10, 10)
    yield pump(0.3)
    check(c().playing, "clic : reprise")
    win._on_click(None, 1, 10, 10)
    win._on_click(None, 2, 10, 10)
    yield Wait(3, until=lambda: win.is_fullscreen())
    check(win.is_fullscreen(), "double-clic : plein écran")
    yield pump(0.3)
    check(c().playing, "double-clic : PAS de pause")
    features.set_enabled("click_pause", False)
    win._on_click(None, 1, 10, 10)
    yield pump(0.3)
    check(c().playing, "clic pour pause coupé : le clic ne fait rien")
    features.set_enabled("click_pause", True)


@step
def plein_ecran_barres(win):
    check(win.toolbar.get_extend_content_to_bottom_edge(), "plein écran : l'image va jusqu'en bas")
    check(win.float_revealer.get_child() is win.controls and "osd" in win.controls.get_css_classes(),
          "plein écran : commandes compactes, flottantes, centrées")
    check(not win.split.get_show_sidebar(), "plein écran : pas de file à côté")
    win._over_bars = False
    win._hide_bars()
    check(not win.controls_visible() and not win.toolbar.get_reveal_top_bars(),
          "plein écran : barres effacées pendant la lecture")
    win._last_motion = (0, 0)
    win._on_motion(None, 50, 60)
    check(win.controls_visible(), "mouvement de la souris : barres revenues")
    c().set_paused(True)
    yield pump(0.2)
    win._hide_bars()
    check(win.controls_visible(), "en pause : les barres restent")
    c().set_paused(False)
    win.activate_action("win.leave-fullscreen", None)
    yield Wait(2, until=lambda: not win.is_fullscreen())
    check(not win.is_fullscreen() and win.controls_visible(), "Échap : fenêtre, barres visibles")
    check(win.controls.get_parent() is not win.float_revealer and "osd" not in win.controls.get_css_classes(),
          "Échap : commandes revenues dans la fenêtre")


@step
def molette_volume(win):
    settings["volume"] = 50
    win._on_wheel(None, 0, -1)
    check(settings["volume"] == 55, "molette vers le haut : +5 %")
    check(win.osd.get_reveal_child() and win.osd.label.get_label() == tr("Volume {0} %").format(55),
          "volume affiché par-dessus l'image")
    win._on_wheel(None, 0, 0.5)
    win._on_wheel(None, 0, 0.5)
    check(settings["volume"] == 50, "pavé tactile : demi-crans additionnés")
    for _ in range(30):
        win._on_wheel(None, 0, -1)
    check(settings["volume"] == 100, "plafond à 100 % sans amplification")
    settings["volume_max"] = 150
    win._on_wheel(None, 0, -1)
    check(settings["volume"] == 105 and "over" in win.osd.level.get_css_classes(), "amplification au-delà de 100 %")
    settings["volume_max"] = 100
    check(settings["volume"] == 100 and abs(c().player.mpv.volume - 100) < 0.5, "amplification coupée : 100 %")
    win.activate_action("win.mute", None)
    check(settings["muted"] and win.osd.label.get_label() == tr("Muet"), "M : muet")
    win.activate_action("win.mute", None)
    features.set_enabled("wheel_volume", False)
    win._on_wheel(None, 0, -1)
    check(settings["volume"] == 100 and not settings["muted"], "molette coupée : volume inchangé")
    features.set_enabled("wheel_volume", True)
    settings["volume"] = 80


@step
def reduite_pause(win):
    check(c().playing, "lecture avant réduction")
    if WM is not None:
        win.minimize()
        yield Wait(3, until=lambda: not c().playing)
        check(not c().playing, "fenêtre réduite (par le gestionnaire de fenêtres) : pause")
        win.present()
        yield Wait(3, until=lambda: c().playing)
        check(c().playing, "fenêtre revenue : la lecture reprend")
    else:
        print("(pas d'openbox : réduction simulée)")
        win.set_minimized_state(True)
        yield pump(0.2)
        check(not c().playing, "fenêtre réduite : pause")
        win.set_minimized_state(False)
        yield pump(0.2)
        check(c().playing, "fenêtre revenue : la lecture reprend")
    c().set_paused(True)
    win.set_minimized_state(True)
    win.set_minimized_state(False)
    yield pump(0.2)
    check(not c().playing, "en pause avant la réduction : reste en pause au retour")
    c().set_paused(False)


@step
def avance_et_file(win):
    c().seek_to(0)
    yield pump(0.3)
    win.activate_action("win.seek-forward", None)
    yield Wait(2, until=lambda: c().player.position() >= 1.5)
    check(win.osd.get_reveal_child(), "→ : avance affichée")
    win.activate_action("win.playlist", None)
    yield pump(0.3)
    check(win.split.get_show_sidebar() and settings["playlist_visible"], "F9 : file affichée (et retenue)")
    panel = win.panel
    check(len(panel._rows) == 3 and panel._rows[1].icon.get_opacity() == 1, "file : 3 lignes, la courante marquée")
    panel.list.unselect_all()
    panel.list.select_row(panel._rows[0])
    panel._on_key(None, Gdk.KEY_Delete, 0, 0)
    yield pump(0.3)
    check(len(c().queue) == 2 and basename() == "Épisode 2.mp4", "Suppr : retiré de la file, lecture inchangée")
    c().move(c().queue.items[1], 0)
    yield pump(0.2)
    check(os.path.basename(c().queue.items[0].path) == "Épisode 10.mp4", "déplacer dans la file")
    win.activate_action("win.next", None)
    yield pump(0.3)
    check(c().queue.pos == 1 or basename() != "Épisode 2.mp4", "suivant")


@step
def illisible_et_fin(win):
    win.open_paths([LIB["fake"], LIB["e1"]])
    yield Wait(10, until=lambda: basename() == "Épisode 1.mp4")
    check(basename() == "Épisode 1.mp4", "fichier illisible : message puis vidéo suivante")
    yield Wait(8, until=lambda: c().player.get("eof-reached"))
    check(not c().playing and win.picture.get_visible_child_name() == "video",
          "fin de la file : dernière image gardée, en pause")
    win.activate_action("win.toggle", None)
    yield Wait(3, until=lambda: c().player.position() < 1.5 and c().playing)
    check(c().playing, "Espace après la fin : relance depuis le début")


@step
def reprise(win):
    win.open_paths([LIB["film"]])
    yield Wait(10, until=lambda: basename() == "Long film.mkv" and c().player.duration() > 0)
    c().seek_to(50)
    yield Wait(4, until=lambda: c().player.position() >= 50)
    win.open_paths([LIB["son"]])
    yield Wait(10, until=lambda: basename() == "chanson.mp3")
    yield Wait(3, until=lambda: win.picture.get_visible_child_name() == "audio")
    check(win.picture.get_visible_child_name() == "audio", "son seul : page avec le titre au lieu d'une image noire")
    win.open_paths([LIB["film"]])
    yield Wait(10, until=lambda: basename() == "Long film.mkv" and c().player.position() >= 49)
    check(c().player.position() >= 49, "vidéo rouverte : reprise où on l'avait laissée")
    c().restart()
    yield Wait(3, until=lambda: c().player.position() < 5)
    check(c().player.position() < 5, "« Depuis le début »")


@step
def capture_image(win):
    win.screenshot()
    yield pump(1.0)
    pics = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or os.environ["HOME"]
    found = [n for n in os.listdir(pics) if n.startswith("Long film") and n.endswith(".png")] \
        if os.path.isdir(pics) else []
    check(bool(found), "S : capture de l'image enregistrée")


@step
def preferences_et_raccourcis(win):
    app._prefs()
    yield pump(0.4)
    prefs = [w for w in Gtk.Window.list_toplevels() if w.get_visible() and w is not win
             and w.get_transient_for() is win]
    check(bool(prefs), "Préférences ouvertes")
    for w in prefs:
        w.close()
    win.show_shortcuts()
    yield pump(0.4)
    shortcuts = [w for w in Gtk.Window.list_toplevels() if isinstance(w, Gtk.ShortcutsWindow) and w.get_visible()]
    check(bool(shortcuts), "fenêtre des raccourcis")
    for w in shortcuts:
        w.close()


@step
def reprendre_la_file(win):
    c().stop()
    c().save()
    yield pump(0.4)
    check(win.picture.get_visible_child_name() == "welcome", "arrêt : accueil")
    check(win.resume_button.get_visible() and "Long film" in win.resume_button.get_label(),
          "accueil : « Reprendre » la dernière vidéo")


def on_window(_a, w):
    if not log:
        log.append("start")
        GLib.timeout_add(500, run_all, w)


app.connect("window-added", on_window)
app.run([sys.argv[0]])
if WM is not None:
    WM.terminate()
shutil.rmtree(TMP, ignore_errors=True)
sys.stderr.flush()
time.sleep(0.2)
for line in CRITICALS:
    errors.append("ÉCHEC : avertissement critique de GTK / GLib : " + line)
print("\n".join(errors) if errors else "\nAucune erreur.")
sys.exit(1 if errors else 0)
