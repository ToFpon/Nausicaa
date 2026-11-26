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
    """L'image de la vidéo est vraiment dessinée (pas noire). Capture de la FENÊTRE entière, pixels lus dans la
    zone de la vidéo : la capture du seul Gtk.GLArea revenait parfois vide alors que mpv dessinait bien
    (13 images rendues, diagnostic) — échec du test une fois sur quatre, jamais de l'appli."""
    view = win.video
    ok, bounds = view.compute_bounds(win)
    if not ok:
        frame_has_colour.why = "compute_bounds"
        return False
    p = Gtk.WidgetPaintable.new(win)
    snap = Gtk.Snapshot()
    p.snapshot(snap, win.get_width(), win.get_height())
    node = snap.to_node()
    if node is None:
        frame_has_colour.why = f"capture vide ({win.get_width()}×{win.get_height()}, visible={win.get_visible()})"
        return False
    tex = win.get_renderer().render_texture(node, None)
    ox, oy = int(bounds.get_x()), int(bounds.get_y())
    w, h = int(bounds.get_width()), int(bounds.get_height())
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
        x, y = min(ox + x, pix.get_width() - 1), min(oy + y, pix.get_height() - 1)
        o = y * stride + x * n
        if max(px[o], px[o + 1], px[o + 2]) > 60:
            bright += 1
    if bright < 2:
        frame_has_colour.why = "image : %dx%d à (%d, %d), échantillons clairs : %d" % (w, h, ox, oy, bright)
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
    # jusqu'à 8 s : sous Xvfb (llvmpipe), la première image tardait parfois plus de 3 s (échec 1 fois sur 4)
    yield Wait(8, until=lambda: frame_has_colour(win))
    if not frame_has_colour(win):
        v = win.video
        print("  diagnostic :", dict(realized=v.get_realized(), mapped=v.get_mapped(), ctx=v.ctx is not None,
                                     gl_error=v.gl_error, size=(v.get_width(), v.get_height()),
                                     opacity=v.get_opacity(), page=win.picture.get_visible_child_name(),
                                     renders=getattr(v, "_renders", "?"), pending=v._pending,
                                     pos=c().player.position(), vid=c().player.get("vid"),
                                     why=getattr(frame_has_colour, "why", "?")))
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
    yield Wait(2, until=lambda: not win.play_flash.get_reveal_child())
    check(not win.play_flash.get_reveal_child(), "ouverture d'une vidéo : pas de signe ⏸ / ▶")
    win._on_click(None, 1, 10, 10)
    yield pump(0.3)
    check(not c().playing, "clic : pause immédiate")
    check(win.play_flash.get_reveal_child() and win.play_flash.icon.get_icon_name() == "media-playback-pause-symbolic",
          "pause : signe ⏸ au centre de l'image")
    yield Wait(2, until=lambda: not win.play_flash.get_reveal_child())
    check(not win.play_flash.get_reveal_child(), "le signe s'efface tout seul")
    win._on_click(None, 1, 10, 10)
    yield pump(0.3)
    check(c().playing, "clic : reprise")
    check(win.play_flash.get_reveal_child() and win.play_flash.icon.get_icon_name() == "media-playback-start-symbolic",
          "reprise : signe ▶")
    yield Wait(2, until=lambda: not win.play_flash.get_reveal_child())
    features.set_enabled("play_flash", False)
    win.activate_action("win.toggle", None)
    yield pump(0.3)
    check(not win.play_flash.get_reveal_child(), "fonctionnalité coupée : pas de signe")
    win.activate_action("win.toggle", None)
    yield pump(0.3)
    features.set_enabled("play_flash", True)
    check(c().playing, "relancée")
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
    check(win.float_revealer.get_child() is win.controls
          and "floating-controls" in win.controls.get_css_classes()
          and "osd" not in win.controls.get_css_classes(),
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
    check(win.controls.get_parent() is not win.float_revealer and "floating-controls" not in win.controls.get_css_classes(),
          "Échap : commandes revenues dans la fenêtre")


def _wm_state(win) -> str:
    """_NET_WM_STATE de la fenêtre, lu par xprop (ce que voit le gestionnaire de fenêtres)."""
    import subprocess
    from nausicaa.ui import x11
    wid = x11.xid(win)
    if wid is None or not shutil.which("xprop"):
        return ""
    out = subprocess.run(["xprop", "-id", str(wid), "_NET_WM_STATE"], capture_output=True, text=True)
    return out.stdout


@step
def image_dans_l_image(win):
    if WM is None:
        print("(pas d'openbox : image dans l'image sautée)")
        return
    width_before = win.get_width()
    win.activate_action("win.pip", None)
    yield Wait(3, until=lambda: win.get_width() <= 420)
    print("  taille :", win.get_width(), win.get_height(), win.get_default_size(), win.is_maximized())
    check(win.pip and win.get_width() <= 420, "image dans l'image : petite fenêtre")
    yield pump(0.7)                              # mise en place terminée (150 et 450 ms)
    check(abs(win.get_width() * 9 / 16 - win.get_height()) < 4,
          f"première fois : 16:9 ({win.get_width()} × {win.get_height()})")
    check(not win.toolbar.get_reveal_top_bars() and not win.controls_visible(), "image seule, sans barres")
    yield Wait(2, until=lambda: "ABOVE" in _wm_state(win))
    check("ABOVE" in _wm_state(win), "toujours au-dessus (X11 : _NET_WM_STATE_ABOVE)")
    check(c().playing, "la lecture continue")
    check(not win.get_decorated(), "aucune décoration (ni ombre, ni bordure)")
    win._on_pointer_leave()                      # (sous Xvfb, le pointeur est déjà sur la fenêtre)
    check(not win.pip_controls.get_reveal_child(), "commandes cachées tant que la souris n'est pas là")
    win._last_motion = (0, 0)
    win._on_motion(None, 30, 30)
    check(not win.toolbar.get_reveal_top_bars(), "le pointeur ne fait pas revenir les barres")
    check(win.pip_controls.get_reveal_child() and win.pip_leave.get_reveal_child(),
          "souris : capsule de commandes et bouton de retour")
    win._over_bars = False
    win._hide_bars()
    check(not win.pip_controls.get_reveal_child(), "souris immobile : commandes effacées")
    win._on_motion(None, 40, 40)
    win._on_pointer_leave()
    check(not win.pip_leave.get_reveal_child(), "souris partie : commandes effacées")
    # glisser l'image : la fenêtre se déplace (plus d'erreur), la pause du clic est annulée
    playing = c().playing
    from gi.repository import Gtk as _Gtk
    drag = [g for g in win.picture.observe_controllers() if isinstance(g, _Gtk.GestureDrag)][0]
    win._on_click(None, 1, 20, 20)
    win._on_drag_begin(drag, 20, 20)
    win._on_drag_update(drag, 30, 30)
    check(c().playing == playing, "glisser : pas de pause")
    # redimensionner : les bords de la fenêtre sont à nous (plus de décorations)
    w, h = win.picture.get_width(), win.picture.get_height()
    check(win._pip_edge(3, 3) == Gdk.SurfaceEdge.NORTH_WEST and win._pip_edge(w - 2, h / 2) == Gdk.SurfaceEdge.EAST
          and win._pip_edge(w / 2, h / 2) is None, "bords et coins reconnus pour redimensionner")
    win._on_picture_motion(None, w - 2, h - 2)
    cursor = win.picture.get_cursor()
    check(cursor is not None and cursor.get_name() == "se-resize", "curseur de redimensionnement dans le coin")
    win._on_picture_motion(None, w / 2, h / 2)
    win._on_click(None, 1, w - 2, h - 2)
    win._on_drag_begin(drag, w - 2, h - 2)
    check(win._dragging_window and c().playing == playing, "glisser un coin : redimensionner, pas de pause")
    from nausicaa.ui import x11 as _x11
    _x11.move(win, 100, 120)                     # comme si on l'avait glissée là
    yield pump(0.5)
    pip_size = (win.get_width(), win.get_height())
    win._on_click(None, 1, 10, 10)
    win._on_click(None, 2, 10, 10)               # double-clic : retour, sans pause
    yield Wait(3, until=lambda: win.get_width() > 420)
    check(not win.pip and win.get_width() > 420, "double-clic : fenêtre d'avant")
    check(abs(win.get_width() - width_before) < 40, "taille d'avant rendue")
    check(c().playing, "double-clic : PAS de pause")
    yield Wait(2, until=lambda: "ABOVE" not in _wm_state(win))
    check("ABOVE" not in _wm_state(win), "plus au-dessus des autres")
    check(win.toolbar.get_reveal_top_bars() and win.controls_visible(), "barres revenues")
    check(win.get_decorated(), "décorations revenues")
    from nausicaa.ui import x11
    yield pump(0.8)
    pos = x11.position(win)
    if pos is not None:
        cx = pos[0] + win.get_width() / 2
        cy = pos[1] + win.get_height() / 2
        check(abs(cx - 640) < 60 and abs(cy - 400) < 60, f"fenêtre revenue au centre de l'écran ({pos})")
    check((settings["pip_x"], settings["pip_y"]) == (100, 120)
          and (settings["pip_width"], settings["pip_height"]) == pip_size, "taille et position retenues")
    win.activate_action("win.pip", None)
    yield Wait(3, until=lambda: win.pip and win.get_width() <= 420)
    yield pump(0.6)
    check(_x11.position(win) == (100, 120) and (win.get_width(), win.get_height()) == pip_size,
          "rouverte au même endroit, à la même taille")
    win.activate_action("win.leave-fullscreen", None)          # Échap
    yield Wait(3, until=lambda: not win.pip)
    check(not win.pip, "Échap : quitte l'image dans l'image")


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
    total = win.controls.total
    total.emit("clicked")
    yield pump(0.3)
    check(total.get_label().startswith("-") and settings["show_remaining"],
          "clic sur la durée : temps restant, choix retenu")
    from nausicaa.ui.controls import Controls
    other = Controls(c())                       # une barre neuve = un prochain lancement
    other.set_duration(c().player.duration())
    check(other.total.get_label().startswith("-"), "temps restant gardé au lancement suivant")
    total.emit("clicked")
    yield pump(0.3)
    check(not total.get_label().startswith("-") and not settings["show_remaining"], "re-clic : durée totale")
    win.activate_action("win.playlist", None)
    yield pump(0.3)
    check(win.split.get_show_sidebar() and settings["playlist_visible"], "F9 : file affichée (et retenue)")
    settings["playlist_side"] = "start"
    check(win.split.get_sidebar_position() == Gtk.PackType.START, "réglage : file à gauche, tout de suite")
    settings["playlist_side"] = "end"
    check(win.split.get_sidebar_position() == Gtk.PackType.END, "réglage : file revenue à droite")
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
def revenir_a_l_accueil(win):
    """Pendant la lecture : bouton ← « Revenir à l'accueil » (demande de Tof) ; file et position gardées."""
    win.open_paths([LIB["film"]])
    yield Wait(10, until=lambda: basename() == "Long film.mkv" and c().player.duration() > 0)
    check(win.home_button.get_visible(), "en lecture : bouton « Revenir à l'accueil » visible")
    c().seek_to(40)
    yield Wait(4, until=lambda: c().player.position() >= 40)
    win.fullscreen()
    yield pump(0.5)
    win.activate_action("win.home", None)
    yield Wait(3, until=lambda: win.picture.get_visible_child_name() == "welcome")
    check(win.picture.get_visible_child_name() == "welcome" and not win.home_button.get_visible(),
          "← : accueil, bouton caché")
    yield Wait(3, until=lambda: not win.is_fullscreen())
    check(not win.is_fullscreen(), "← en plein écran : plein écran quitté")
    check(c().positions.get(LIB["film"]) >= 39, "position retenue (Reprendre)")
    check(len(c().queue) > 0 and c().player.path is None, "file gardée, lecture arrêtée")
    data = c().saved_queue(c().queue_file)
    check(bool(data and data.get("current")), "file enregistrée : « Reprendre » sur l'accueil")


@step
def passer_le_generique(win):
    """« Passer le générique » : chapitre « Credits » → bouton « Épisode suivant » ; sans chapitres, durée apprise
    par « Le générique commence ici » (G) pour toute la série (saisons comprises), croix, mode automatique, oubli."""
    import subprocess
    show = os.path.join(TMP, "generique")
    e1 = media.long_video(os.path.join(show, "Show saison 1", "Show S01E01.mkv"), 320,
                          [(0, "Intro"), (30, "Épisode"), (300, "Credits")])
    plain = []
    for name in ("Show saison 1/Show S01E02.mkv", "Show saison 2/Show S02E01.mkv", "Show saison 2/Show S02E02.mkv"):
        out = os.path.join(show, name)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", e1, "-map", "0", "-map_chapters", "-1",
                        "-c", "copy", out], check=True)
        plain.append(out)
    e2, s2e1, s2e2 = plain
    win.open_paths([e1])                                 # « Épisodes suivants » : E02 vient avec
    yield Wait(10, until=lambda: basename() == "Show S01E01.mkv" and win._credits is not None)
    found = win._credits
    check(found is not None and abs(found.start - 300) < 0.5 and found.source == "chapters",
          f"générique trouvé dans les chapitres (« Credits » à 5:00) : {found}")
    c().seek_to(200)
    yield pump(0.6)
    check(not win.credits_showing(), "avant le générique : pas de bouton")
    c().seek_to(301)
    yield Wait(4, until=win.credits_showing)
    check(win.credits_showing() and win.credits_content.get_label() == tr("Épisode suivant"),
          "pendant le générique : bouton « Épisode suivant »")
    win.credits_button.emit("clicked")
    yield Wait(8, until=lambda: basename() == "Show S01E02.mkv")
    check(basename() == "Show S01E02.mkv" and not win.credits_showing(), "clic : épisode suivant")

    # sans chapitres : rien, jusqu'à ce qu'on l'apprenne
    yield Wait(6, until=lambda: c().player.duration() > 0)
    c().seek_to(80)
    yield Wait(4, until=lambda: c().player.position() >= 80)
    win.activate_action("win.credits-here", None)
    check(win.credits_store.get(e2) is None, "« Le générique commence ici » au début de l'épisode : refusé")
    c().seek_to(305)
    yield Wait(4, until=lambda: c().player.position() >= 305)
    check(win._credits is None and not win.credits_showing(), "sans chapitres ni durée apprise : pas de bouton")
    win.activate_action("win.credits-here", None)
    left = win.credits_store.get(e2) or 0
    check(14 <= left <= 16, f"G : durée retenue pour la série ({left:.1f} s)")
    yield pump(0.6)
    check(not win.credits_showing(), "le générique qu'on vient de marquer : pas de bouton tout de suite")

    # autre saison, même série : la durée apprise sert
    win.open_paths([s2e1])
    yield Wait(10, until=lambda: basename() == "Show S02E01.mkv" and win._credits is not None)
    check(win._credits is not None and win._credits.source == "learned", "saison 2 : durée apprise reprise")
    c().seek_to(306)
    yield Wait(4, until=win.credits_showing)
    check(win.credits_showing(), "saison 2 : bouton au même moment")
    win._dismiss_credits()
    c().seek_to(308)
    yield pump(0.6)
    check(not win.credits_showing(), "croix : on continue à regarder, le bouton ne revient pas")

    # automatique (Préférences → Lecture → Générique de fin : Passer tout seul)
    settings["credits_auto"] = True
    before = c().item
    win.open_paths([s2e1])                            # la même vidéo rouverte : le générique est reproposé
    yield Wait(10, until=lambda: c().item is not before and c().player.duration() > 0 and win._credits is not None)
    c().seek_to(306)
    yield Wait(8, until=lambda: basename() == "Show S02E02.mkv")
    check(basename() == "Show S02E02.mkv", "« Passer tout seul » : épisode suivant sans clic")
    settings["credits_auto"] = False

    win.activate_action("win.credits-forget", None)
    check(win.credits_store.get(s2e2) is None, "« Oublier le générique de cette série »")
    yield pump(0.3)
    check(win._credits is None, "oublié : plus de générique pour cette série")


@step
def plein_ecran_a_l_ouverture(win):
    check(not win.is_fullscreen(), "fenêtré avant l'essai")
    features.set_enabled("auto_fullscreen", True)
    try:
        win.open_paths([LIB["pochette"]])
        yield Wait(10, until=lambda: basename() == "avec pochette.mp3" and c().player.tracks("audio"))
        yield pump(1.0)
        check(any(t.get("albumart") for t in c().player.tracks("video")), "MP3 : pochette vue comme piste vidéo")
        check(not win.is_fullscreen(), "plein écran à l'ouverture : pas pour un son avec pochette")
        win.open_paths([LIB["e1"]], replace=False)               # ajout à la file : pas de plein écran
        yield pump(1.0)
        check(not win.is_fullscreen(), "plein écran à l'ouverture : pas pour un ajout à la file")
        win.open_paths([LIB["film"]])
        yield Wait(10, until=lambda: basename() == "Long film.mkv" and win.is_fullscreen())
        check(win.is_fullscreen(), "plein écran à l'ouverture : la vidéo passe en plein écran")
        win.activate_action("win.leave-fullscreen", None)
        yield Wait(2, until=lambda: not win.is_fullscreen())
        win.open_paths([LIB["e1"]], replace=False)
        yield Wait(5, until=lambda: any(i.path == LIB["e1"] for i in c().queue.items))
        c().play_index(len(c().queue.items) - 1)
        yield Wait(5, until=lambda: basename() == "Épisode 1.mp4")
        yield pump(1.0)
        check(not win.is_fullscreen(), "Échap puis vidéo suivante : pas remis de force en plein écran")
    finally:
        features.set_enabled("auto_fullscreen", False)
    win.open_paths([LIB["film"]])
    yield Wait(10, until=lambda: basename() == "Long film.mkv" and c().player.duration() > 0)
    yield pump(1.0)
    check(not win.is_fullscreen(), "option coupée : la vidéo s'ouvre dans la fenêtre")


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


@step
def bibliotheque(win):
    """Accueil « bibliothèque » (option) : séries rangées « Toto saison N », films à la racine, affiches tirées
    des vidéos, page d'une série, épisode lu avec la série dans la file, vu / non vu, option coupée en direct."""
    from nausicaa.backend import library
    from nausicaa.ui.library import Card
    root = os.path.join(TMP, "Vidéos")
    for rel in ("Toto saison 1/Toto S01E01.mkv", "Toto saison 1/Toto S01E02.mkv", "Toto saison 2/Toto S02E01.mkv",
                "Le.Film.2019.1080p.mkv"):
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        media.video(os.path.join(root, rel), 2)
    settings["library_dirs"] = [root]
    check(win.welcome_stack.get_visible_child_name() == "classic", "option coupée : accueil classique")
    features.set_enabled("library", True)
    lib = win.library
    check(lib is not None and win.welcome_stack.get_visible_child_name() == "library",
          "option activée : bibliothèque sur l'accueil, sans redémarrer")
    yield Wait(10, until=lambda: lib.lib.shows and lib.lib.films)
    check([s.name for s in lib.lib.shows] == ["Toto"] and len(lib.lib.shows[0].seasons) == 2,
          "« Toto saison 1 » et « Toto saison 2 » : une série, deux saisons")
    check([library.clean_title(p) for p in lib.lib.films] == ["Le Film (2019)"], "film à la racine, titre lisible")
    titles = [w.get_label() for w in _all(lib, Gtk.Label) if "title-3" in w.get_css_classes()]
    check(titles == [tr("Séries"), tr("Films")], f"sections ({titles})")
    yield Wait(20, until=lambda: all(c.picture.get_visible() for cs in lib.cards.values() for c in cs))
    check(all(c.picture.get_visible() for cs in lib.cards.values() for c in cs), "affiches tirées des vidéos")
    show_card = lib.cards["show:toto"][0]
    check(isinstance(show_card, Card), "carte de la série")
    lib.card_menu(show_card, 20, 20)
    yield pump(0.3)
    pop = lib._last_popover
    check(pop is not None and pop.get_visible(), "clic droit : menu de la carte")
    if pop is not None:
        pop.popdown()
    yield pump(0.2)
    film_card = next(c for k, cs in lib.cards.items() if not k.startswith("show:") for c in cs)
    lib.card_menu(film_card, 20, 20)
    yield pump(0.3)
    check(lib._last_popover is not pop and lib._last_popover.get_visible(),
          "autre forme de menu : son propre menu (pas la hauteur de l'autre, vu tronqué chez Tof)")
    sws = [w for w in _all(lib._last_popover, Gtk.ScrolledWindow)]
    adj = sws[0].get_vadjustment() if sws else None
    check(adj is not None and adj.get_page_size() >= adj.get_upper() - 0.5,
          f"menu en entier, sans défilement ({adj.get_page_size() if adj else '?'} / {adj.get_upper() if adj else '?'} ; "
          "accroché au Gtk.ScrolledWindow, GTK 4.14 le tronquait)")
    lib._last_popover.popdown()
    lib.card_menu(show_card, 20, 20)
    yield pump(0.3)
    check(lib._last_popover is pop, "même forme : le même menu, réutilisé (jamais détruit)")
    pop.popdown()
    yield pump(0.2)
    lib.refresh(force=True)                       # cartes reconstruites, menu gardé : pas de plantage
    yield Wait(5, until=lambda: not lib._scanning)
    yield pump(0.3)
    show_card = lib.cards["show:toto"][0]
    show_card.emit("clicked")
    yield pump(0.3)
    check(lib.get_visible_child_name() == "show", "clic : page de la série")
    rows = [r for r in _all(lib.show_box, Gtk.Widget) if hasattr(r, "_path")]
    check(len(rows) == 3, f"trois épisodes ({len(rows)})")
    rows[1].emit("activated")
    yield Wait(10, until=lambda: basename() == "Toto S01E02.mkv")
    check(basename() == "Toto S01E02.mkv", "épisode lu")
    check([os.path.basename(i.path) for i in c().queue.items] ==
          ["Toto S01E01.mkv", "Toto S01E02.mkv", "Toto S02E01.mkv"], "toute la série dans la file, saisons comprises")
    yield Wait(10, until=lambda: basename() == "Toto S02E01.mkv")
    check(basename() == "Toto S02E01.mkv", "fin de saison 1 : la saison 2 suit")
    c().stop()
    yield pump(0.4)
    check(win.picture.get_visible_child_name() == "welcome", "arrêt : retour à la bibliothèque")
    yield Wait(5, until=lambda: lib.entries)
    ep2 = os.path.join(root, "Toto saison 1", "Toto S01E02.mkv")
    check(bool(lib.entries.get(ep2, {}).get("seen")), "épisode lu jusqu'au bout : vu")
    lib._menu_path = ep2
    lib._mark(False)
    yield Wait(5, until=lambda: not lib.entries.get(ep2, {}).get("seen"))
    check(not lib.entries.get(ep2, {}).get("seen"), "« Marquer comme non vu »")
    lib._menu_path = ep2
    lib._mark(True)
    yield Wait(5, until=lambda: lib.entries.get(ep2, {}).get("seen"))
    check(bool(lib.entries.get(ep2, {}).get("seen")), "« Marquer comme vu »")
    # « Reprendre » : croix au survol / « Retirer de Reprendre », sans perdre la position
    epr = os.path.join(root, "Toto saison 1", "Toto S01E01.mkv")
    c().positions.data[__import__("nausicaa.backend.positions", fromlist=["x"]).key_for(epr)] = \
        {"pos": 120, "dur": 1800, "time": int(time.time())}
    lib.refresh(force=True)
    yield Wait(5, until=lambda: any(getattr(c_, "resume_path", None) == epr for cs in lib.cards.values() for c_ in cs))
    tiles = [w for w in _all(lib, Gtk.Overlay) if hasattr(w, "close")]
    check(len(tiles) == 1 and tiles[0].close.get_opacity() == 0, "« Reprendre » : croix cachée tant qu'on ne survole pas")
    tiles[0].close.emit("clicked")
    yield pump(0.3)
    check(not [w for w in _all(lib, Gtk.Overlay) if hasattr(w, "close")], "croix : retirée de « Reprendre »")
    check(c().positions.get(epr) == 120, "… sans perdre la position (la reprise marche toujours)")
    c().positions.hide(epr, False)
    lib.refresh(force=True)
    yield Wait(5, until=lambda: [w for w in _all(lib, Gtk.Overlay) if hasattr(w, "close")])
    card = [w for w in _all(lib, Gtk.Overlay) if hasattr(w, "close")][0].get_child()
    lib.card_menu(card, 20, 20)
    yield pump(0.3)
    lib._last_popover.popdown()
    lib.activate_action("library.hide-resume", None)
    yield pump(0.3)
    check(not [w for w in _all(lib, Gtk.Overlay) if hasattr(w, "close")], "clic droit « Retirer de Reprendre »")
    film = lib.lib.films[0]
    lib.play_film(film)
    yield Wait(10, until=lambda: c().player.path == film)
    check([i.path for i in c().queue.items] == [film], "film : lu seul (pas les autres vidéos du dossier)")
    c().stop()
    yield pump(0.3)
    # affiches de TMDB (faux serveur de test_tmdb) : clé de l'utilisateur, refus signalé
    import test_tmdb
    from nausicaa.backend import tmdb
    tmdb.API, tmdb.IMG = test_tmdb.BASE + "/3", test_tmdb.BASE + "/img"
    media.video(os.path.join(root, "Malcolm saison 1", "Malcolm S01E01.mkv"), 2)
    settings["tmdb_key"] = test_tmdb.KEY
    features.set_enabled("tmdb", True)
    from nausicaa.i18n import current_language
    want = lambda: tmdb.poster("tv", "Malcolm", "", test_tmdb.KEY, current_language())  # noqa: E731
    yield Wait(15, until=lambda: lib.posters.get("show:malcolm") == posters_cache(want()))
    check(lib.posters.get("show:malcolm") == posters_cache(want()),
          f"TMDB : affiche de la série ({lib.posters.get('show:malcolm')} / {posters_cache(want())})")
    check(lib.posters.get("show:toto", "").endswith(".jpg"), "série inconnue de TMDB : image tirée de la vidéo")
    settings["tmdb_key"] = "mauvaise"
    yield Wait(10, until=lambda: "key" in lib._told)
    check("key" in lib._told, "clé refusée : signalée (une fois)")
    features.set_enabled("tmdb", False)
    settings["tmdb_key"] = ""
    yield pump(0.3)
    features.set_enabled("library", False)
    check(win.welcome_stack.get_visible_child_name() == "classic", "option coupée : accueil classique, en direct")


def posters_cache(src):
    from nausicaa.backend import posters
    return posters.cache_path(src, "tmdb") if src else None


def _all(widget, cls):
    out = []

    def walk(w):
        if isinstance(w, cls):
            out.append(w)
        ch = w.get_first_child()
        while ch is not None:
            walk(ch)
            ch = ch.get_next_sibling()
    walk(widget)
    return out


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
