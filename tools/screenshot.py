#!/usr/bin/env python3
"""Capture de la fenêtre de Nausicaa (à lancer sous xvfb-run) pour la documentation et le site.

    xvfb-run -a -s "-screen 0 1280x800x24" python3.12 tools/screenshot.py SORTIE.png [options]

Options : --dark --size=LxH --delay=ms --welcome (page d'accueil) --playlist (file affichée)
          --volume (volume affiché sur l'image) --fullscreen (plein écran, barres affichées)
          --pip (image dans l'image, écran réel)
          --source=FILM [--episode=N] (six extraits d'une vraie vidéo au lieu des fractales ; pour le site :
          Big Buck Bunny, voir BBB_CUTS)
          --prefs=page (general, features) --css=FICHIER (couleurs d'un thème à imiter)
          --library (accueil bibliothèque : vidéothèque de démonstration) [--show=NOM (page d'une série)]
Vidéos d'exemple générées par ffmpeg (fractales), sortie son factice, configuration jetable.
--fullscreen lance openbox et capture l'écran réel (`import -window root`) : la capture d'un widget
peut rendre une image en retard pour la barre flottante.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="nausicaa-shot-")
for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
    os.environ[var] = os.path.join(TMP, sub)
os.environ["NAUSICAA_AO"] = "null"
sys.path.insert(0, ROOT)

out = os.path.abspath(sys.argv[1])
opts = {}
for a in sys.argv[2:]:
    if a.startswith("--"):
        k, _, v = a[2:].partition("=")
        opts[k] = v
delay = int(opts.get("delay", 4000))

WM = None
SCREEN = "fullscreen" in opts or "pip" in opts       # capture de l'écran réel, gestionnaire de fenêtres
if SCREEN and shutil.which("openbox"):
    WM = subprocess.Popen(["openbox", "--sm-disable"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402

from nausicaa.app import NausicaaApp  # noqa: E402
from nausicaa.settings import settings  # noqa: E402

SOURCES = [
    "mandelbrot=size=640x360:rate=25",
    "mandelbrot=size=640x360:rate=25:start_x=-0.743643887:start_y=0.131825904:start_scale=0.004:inner=period",
    "mandelbrot=size=640x360:rate=25:start_x=-1.25066:start_y=0.02012:start_scale=0.02:outer=normalized_iteration_count",
    "mandelbrot=size=640x360:rate=25:start_x=0.2549870375144766:start_y=-0.0005679790528465:start_scale=0.01",
]
TITLES = ["The Shore", "The Stranger", "The Palace", "The Games", "The Song", "The Ship"]


# --source=FILM : six « épisodes » découpés dans une vraie vidéo (début, titre). Pour le site : Big Buck Bunny
# (© 2008 Blender Foundation, CC BY 3.0, peach.blender.org), version 1080p de download.blender.org/demo/movies/BBB/.
# L'image capturée est celle de début + 9 s (seek_to 9) : brigands 5:22, papillon 1:22, écureuil volant 7:15
# (vérifié image par image ; une planche « fps=1/15 » décale ses étiquettes de 7,5 s).
BBB_CUTS = [(13, "Morning"), (313, "The Bullies"), (73, "The Butterfly"), (163, "The Apple"), (268, "The Trap"),
            (426, "Flying Squirrel")]


def demo_from(source: str, episode: int) -> str:
    folder = os.path.join(TMP, "Videos", "Big Buck Bunny")
    os.makedirs(folder)
    for i, (start, title) in enumerate(BBB_CUTS, 1):
        path = os.path.join(folder, f"Big Buck Bunny - {i} - {title}.mkv")
        # -map_metadata -1 : sans le titre du film entier (« Big Buck Bunny, Sunflower version »), que Nausicaa
        # afficherait à la place du nom de l'épisode (fonctionnalité file_titles)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(start), "-i", source, "-map_metadata", "-1",
                        "-t", "16",
                        "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", path], check=True)
    i = max(1, min(episode, len(BBB_CUTS)))
    return os.path.join(folder, f"Big Buck Bunny - {i} - {BBB_CUTS[i - 1][1]}.mkv")


def demo_series() -> str:
    """« Phaeacia » : six épisodes (fractales qui zooment), un son, de quoi remplir la file.
    Avec --source=FILM : six extraits d'une vraie vidéo (--episode=N : celui qui est lu, 2 par défaut)."""
    if opts.get("source"):
        return demo_from(opts["source"], int(opts.get("episode", 2)))
    folder = os.path.join(TMP, "Videos", "Phaeacia")
    os.makedirs(folder)
    for i, title in enumerate(TITLES, 1):
        path = os.path.join(folder, f"Phaeacia - Episode {i} - {title}.mkv")
        src = SOURCES[(i - 1) % len(SOURCES)] + ":end_scale=0.0003"
        seconds = 16 if i == 2 else 3
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 60 * i}:duration={seconds}", "-t", str(seconds),
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", path],
                       check=True)
    return os.path.join(folder, "Phaeacia - Episode 2 - The Stranger.mkv")


def demo_library() -> str:
    """Vidéothèque rangée comme chez Tof : « Série saison N » et films à la racine. Avec --source : extraits de
    la vraie vidéo (affiches tirées de vraies images), sinon fractales."""
    root = os.path.join(TMP, "Vidéos")
    shows = [("Phaeacia saison 1", 3), ("Phaeacia saison 2", 2), ("Ithaque saison 1", 2)]
    films = ["Le.Voyage.2019.1080p.BluRay.x264.mkv", "Calypso (2021).mkv", "Les Sirènes.mkv", "Charybde et Scylla.mkv"]
    n = 0

    def make(path, i):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if opts.get("source"):
            start = BBB_CUTS[i % len(BBB_CUTS)][0] + 6
            cmd = ["ffmpeg", "-loglevel", "error", "-y", "-ss", str(start), "-i", opts["source"], "-t", "4",
                   "-vf", "scale=640:-2", "-c:v", "libx264", "-preset", "veryfast", "-an", path]
        else:
            src = SOURCES[i % len(SOURCES)] + ":end_scale=0.0003"
            cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src, "-t", "3",
                   "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path]
        subprocess.run(cmd, check=True)

    for folder, count in shows:
        name = folder.split(" saison")[0]
        num = folder.rsplit(" ", 1)[1]
        for e in range(1, count + 1):
            make(os.path.join(root, folder, f"{name} S{int(num):02d}E{e:02d}.mkv"), n)
            n += 1
    for f in films:
        make(os.path.join(root, f), n)
        n += 1
    return root


def demo_history(win, root):
    """Un épisode vu, un commencé, un film commencé : coches et barres d'avancement."""
    from nausicaa.backend import positions
    pos = app.controller.positions
    now = int(time.time())
    for rel, entry in (("Phaeacia saison 1/Phaeacia S01E01.mkv", {"pos": 0, "dur": 2700, "seen": True}),
                       ("Phaeacia saison 1/Phaeacia S01E02.mkv", {"pos": 1500, "dur": 2700}),
                       ("Calypso (2021).mkv", {"pos": 2400, "dur": 6300}),
                       ("Les Sirènes.mkv", {"pos": 0, "dur": 5400, "seen": True}),
                       ("Ithaque saison 1/Ithaque S01E01.mkv", {"pos": 0, "dur": 2700, "seen": True})):
        k = positions.key_for(os.path.join(root, rel))
        if k:
            pos.data[k] = dict(entry, time=now)
            now -= 60


def snap(win):
    def do():
        if SCREEN:
            subprocess.run(["import", "-window", "root", out], check=True)
            print("capture", out, "(écran)")
        else:
            paint = Gtk.WidgetPaintable.new(win)
            w, h = win.get_width(), win.get_height()
            snapshot = Gtk.Snapshot()
            paint.snapshot(snapshot, w, h)
            for d in Gtk.Window.list_toplevels():
                if d is not win and d.get_visible() and d.get_transient_for() is win:
                    dw, dh = d.get_width(), d.get_height()
                    snapshot.save()
                    snapshot.translate(Graphene.Point().init(max((w - dw) / 2, 0), max((h - dh) / 2, 0)))
                    Gtk.WidgetPaintable.new(d).snapshot(snapshot, dw, dh)
                    snapshot.restore()
            tex = win.get_renderer().render_texture(snapshot.to_node(), None)
            tex.save_to_png(out)
            print("capture", out, w, h)
        app._quit()
        return False
    GLib.timeout_add(delay, do)


app = NausicaaApp(os.path.join(TMP, "queue.json"))


def on_ready(win):
    if getattr(app, "_shot", False):
        return False
    app._shot = True
    if "dark" in opts:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    if opts.get("css"):
        # imiter un thème (couleurs redéfinies par @define-color, comme un gtk.css d'utilisateur)
        from gi.repository import Gdk
        provider = Gtk.CssProvider()
        provider.load_from_path(opts["css"])
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)
    w, _, h = opts.get("size", "1000x620").partition("x")
    win.set_default_size(int(w), int(h))
    if "left" in opts:                          # file de lecture à gauche
        settings["playlist_side"] = "start"
    win.split.set_show_sidebar("playlist" in opts)
    if "library" in opts:
        root = demo_library()
        demo_history(win, root)
        settings["library_dirs"] = [root]
        from nausicaa import features
        features.set_enabled("library", True)
        win._sync_page()
        if opts.get("show"):
            def show():
                lib = win.library
                if lib is None or not lib.lib.shows:
                    return True
                s = next((x for x in lib.lib.shows if x.name == opts["show"]), lib.lib.shows[0])
                lib.open_show(s)
                return False
            GLib.timeout_add(1500, show)
    elif "welcome" not in opts:
        first = demo_series()
        win.open_paths([first])
        # image arrêtée au même endroit à chaque capture : en pause sur une belle vue
        GLib.timeout_add(1500, lambda: (app.controller.seek_to(9.0), app.controller.set_paused(True), False)[2])
    if "fullscreen" in opts:
        GLib.timeout_add(1800, lambda: (win.fullscreen(), False)[1])
        GLib.timeout_add(delay - 900, lambda: (win._reveal_bars(True), False)[1])
    if "pip" in opts:
        GLib.timeout_add(1800, lambda: (win.enter_pip(), False)[1])
        GLib.timeout_add(delay - 700, lambda: (win._reveal_bars(True), False)[1])     # commandes au survol
    if "volume" in opts:
        GLib.timeout_add(delay - 600, lambda: (settings.__setitem__("volume", 70), win.show_volume(), False)[2])
    if "prefs" in opts:
        def prefs():
            from nausicaa.ui.preferences import PreferencesWindow
            d = PreferencesWindow()
            if opts["prefs"]:
                d.set_visible_page_name(opts["prefs"])
            d.set_transient_for(win)
            d.present()
            return False
        GLib.timeout_add(delay - 1000, prefs)
    snap(win)
    return False


app.connect("window-added", lambda _a, w: GLib.idle_add(on_ready, w))
status = app.run([sys.argv[0]])
if WM is not None:
    WM.terminate()
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(status)
