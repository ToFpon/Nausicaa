# Nausicaa — notes de travail

Lecteur vidéo GTK4 / libadwaita en Python sur libmpv, dans l'esprit de Celluloid (simple, sans Flatpak),
avec les gestes de souris de VLC. Ossature reprise d'Ithaca (`ToFpon/Ithaca`, elle-même reprise
d'Odysseus) : i18n, réglages, fonctionnalités, vrais dialogues, file de lecture, listes de lecture,
tests, paquet.

## Règles

- Répondre à Tof en français, tutoiement, avis francs. Messages de commit en français.
- Cible : Zorin OS 18.1 (Ubuntu 24.04, GNOME 46, X11, GTK 4.14, libadwaita 1.5, libmpv2 0.37,
  python3-mpv 1.0.4, Python 3.12). Pas d'API plus récente (pas d'Adw.Dialog, Adw.AlertDialog…).
  Paquets apt seulement.
- Thème GTK du système : `data/base.css` ne fait que de la mise en page. Seule couleur fixe : le noir
  derrière l'image (bandes autour d'un film), comme tout lecteur vidéo.
- Dialogues : de vraies fenêtres (`common.AlertDialog`, `common.DialogWindow`, `Adw.PreferencesWindow`,
  `Adw.AboutWindow`).
- Chaque fonctionnalité facultative est dans `nausicaa/features.py` (click_pause, wheel_volume,
  minimize_pause, resume, next_episodes, hwdec) et se consulte au moment de l'usage.
- Tout texte visible passe par `tr()` / `ntr()`. Après modification : `python3.12 tools/update-pot.py`,
  compléter `data/locale/en.po` et `de.po`, puis `msgfmt -c --statistics` et `msgcmp`.
- Toute fonction passée à `idle_add` / `timeout_add` renvoie False (sauf répétition voulue).
- Commandes : comme Ithaca (fichiers par Edit / Write, une action git par commande avec `git -C`,
  message long par `commit -F`).

## Architecture

- `ui/video.py` : `VideoView(Gtk.GLArea)` — mpv dessine par son API de rendu OpenGL
  (`MpvRenderContext`, comme Celluloid) ; widget GTK ordinaire, X11 et Wayland, pas d'identifiant de
  fenêtre. Fonctions OpenGL demandées à EGL ou GLX selon GTK (`_uses_glx`). Rendu : FBO de GTK lu par
  `glGetIntegerv(GL_FRAMEBUFFER_BINDING)`, `flip_y`. `update_cb` vient du fil de mpv → un seul
  `idle_add` en attente. Pièges :
  - mpv (vo=libmpv) refuse d'ouvrir une vidéo tant que le contexte de rendu n'existe pas (le fichier
    « ne peut pas être lu ») : `VideoView.when_ready(cb)` ; la fenêtre ouvre tout par là. La vidéo est
    donc TOUJOURS réalisée : l'accueil et la page « son seul » sont posés dessus (`PictureArea`,
    Gtk.Overlay), jamais un Gtk.Stack (il ne réalise que la page affichée).
  - Fermeture : contexte de rendu libéré (fenêtre détruite) AVANT `mpv.terminate()`, sinon « Aborted ».
    `app._close_window` : stop, teardown, destroy, boucle vidée, puis `controller.shutdown()`.
  - Rendu logiciel (llvmpipe : machine virtuelle, Xvfb) : les filtres d'échelle par défaut de mpv
    dessinent une grille de rayures noires → `scale/dscale/cscale=bilinear` seulement dans ce cas
    (`GL_RENDERER` lu à la réalisation).
  - Relire l'image par `glReadPixels` après `ctx.render` donne du noir (mpv change de FBO) : vérifier
    l'image par `Gtk.WidgetPaintable` (ui_smoke `frame_has_colour`).
- `backend/player.py` : `mpv.MPV` (keep_open : c'est nous qui enchaînons, jamais la liste interne de
  mpv ; pas de raccourcis ni d'OSC de mpv). Propriétés observées → `_post` (une rafale = un seul
  idle_add) → écouteurs dans le fil principal. `LC_NUMERIC=C` obligatoire. `loadfile` en arguments
  nommés (mpv 0.37 et 0.38+), options en CHAÎNE « start=12.3 » (un dictionnaire est refusé).
  `set_paused` note la pause tout de suite (mpv la confirme plus tard : clic + double-clic voyaient
  l'ancienne valeur) ; vidéo finie : bascule = relancer (mpv annonce sa pause de fin après eof-reached).
- `backend/controller.py` : file + lecteur ; événements item / state / position / duration / queue /
  modes / volume / tracks / video / title / resumed / ended / error / loading. Fin d'une vidéo →
  position oubliée, suivante selon la répétition ; illisible → message et suivante. Volume 0..100
  (150 avec « Volume maximal »). `NAUSICAA_AO=null` : sortie son factice (tests).
- `backend/positions.py` : reprise par chemin réel + taille ; rien avant 15 s, rien pour < 60 s, fin
  (5 %, 20 s au moins) = vue. `backend/media.py` : extensions, ordre naturel (`name_key`, ctypes, en
  octets, repris d'Odysseus), `expand`, `siblings` (épisodes suivants). `playlists.py` / `playqueue.py` :
  repris d'Ithaca.
- `ui/window.py` : ToolbarView (en-tête, commandes en bas) dans un OverlaySplitView (file à droite).
  Fenêtré : barres toujours visibles ; plein écran : image jusqu'aux bords, barres + pointeur effacés
  après 2 s pendant la lecture (pas sur une barre, pas avec un menu ouvert, pas en pause). Clic gauche
  = bascule immédiate ; 2e clic du double-clic = bascule inverse + plein écran (`_click_toggled`).
  Réduction : X11 MINIMIZED, Wayland SUSPENDED (`_hidden_flags`) ; reprise seulement si c'est nous qui
  avons mis en pause. `ui/osd.py` : encart « osd » du thème. `ui/controls.py`, `ui/playlist_panel.py`
  (ListBox, glisser pour réordonner), `ui/shortcuts.py` (ShortcutsWindow en XML).
- `app.py` : instance unique, HANDLES_OPEN + ligne de commande (`--enqueue`), fermer = quitter,
  inhibition IDLE | SUSPEND pendant la lecture.

## Tests

```sh
python3.12 -m unittest discover -s tests            # moteur (vrai mpv, vo/ao factices)
xvfb-run -a -s "-screen 0 1280x800x24" python3.12 tests/ui_smoke.py   # doit finir par « Aucune erreur. »
LANGUAGE=en … ; LANGUAGE=de …
python3 -m pyflakes nausicaa tests tools
```

ui_smoke lance openbox s'il existe (plein écran et réduction passent par le gestionnaire de fenêtres ;
sans lui, réduction simulée). Vidéos de test : `tests/media.py` (ffmpeg, x264 ultrafast).

## Publication (comme Ithaca, pas encore en place)

PPA à créer (`ppa:nourpon/nausicaa`, `tools/ppa.sh`), workflow `release-deb.yml` repris d'Ithaca.
Paquet vérifié ici : dpkg-buildpackage, lintian sans remarque, appstreamcli validate (note pédante).
