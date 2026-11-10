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
  minimize_pause, resume, next_episodes, mpris, hwdec) et se consulte au moment de l'usage.
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
    Gtk.Overlay), jamais un Gtk.Stack (il ne réalise que la page affichée). Sur ces pages : vidéo à
    opacité 0, classe `.page` (fond transparent) → le fond de fenêtre du thème se voit, comme Ithaca
    (validé chez Tof, 0.1.7 ; sous Xvfb sans compositeur, `window.csd` ne s'applique pas : tester avec
    `--css` sur `window.background`).
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
  Plein écran : la barre de commandes QUITTE l'Adw.ToolbarView pour `float_revealer` (Gtk.Revealer posé
  sur l'image, centré en bas, 60 % de l'écran entre 480 et 820 px) — demande
  de Tof (comme Celluloid / VLC) ; dans la zone du bas de la ToolbarView, coins et ombre étaient rognés.
  `controls_visible()` pour les tests. Capsule (`.floating-controls`) et encart du volume (`.nausicaa-osd`) :
  `alpha(@window_bg_color, 0.72)` (translucide : l'image se devine, validé chez Tof en 0.1.5 ; aucun flou
  possible — Blur my Shell floute le bureau DERRIÈRE la fenêtre, pas la vidéo dedans, et GTK 4.14 n'a pas
  de backdrop-filter) + `@view_bg_color` posé dessus (dégradé uni), texte `@view_fg_color`, barres en
  couleur d'accent — comme les Propriétés d'Odysseus chez Tof (son thème : view_bg_color = violet à 20 % ;
  window_bg_color seul = le gris des listes, refusé). Pas la classe « osd » de libadwaita (noir, barre
  blanche). En-tête du plein écran : style du thème, rien d'imposé. Validé chez Tof (0.1.4).
  `tools/screenshot.py --css=FICHIER` imite un thème (@define-color).
- Image dans l'image (Ctrl+P, bouton à icône à nous `data/icons/…/nausicaa-pip-symbolic.svg` — Adwaita 46
  n'en a pas —, menus ; demande de Tof, X11 seulement voulu) : `MainWindow.enter_pip / leave_pip`. En-tête et
  barre de commandes `set_visible(False)` (un Gtk.Revealer fermé garde la LARGEUR minimale de son contenu :
  la fenêtre ne descendait pas sous ~530 px), file cachée, 28 % de l'écran (400 à 640 px) à la proportion
  de l'image. Sans décoration (`set_decorated(False)`), coins carrés et fond noir par `data/pip.css` chargé
  à USER + 1 (le gtk.css de Tof, priorité USER, arrondissait `window.csd` et laissait des restes violets aux
  coins — une priorité plus haute gagne, quelle que soit la spécificité). Bords à nous : 12 px près d'un
  bord / coin = curseur de redimensionnement et `begin_resize` (`_pip_edge`). Au survol : capsule
  précédent / lecture / suivant et bouton de retour en haut à gauche. Sortie : fenêtre recentrée.
  Première fois en 16:9 (bandes noires pour un film en 2.39, comme Firefox) ; ensuite taille et position
  retenues (`pip_width/height/x/y`, à la sortie et à la fermeture) — position gardée seulement si la fenêtre
  y tient encore entière, sinon le coin. Taille par défaut ≠ taille vue sans décoration (400 × 225 →
  390 × 215) : écart mesuré à la mise en place et compensé (`_pip_frame`). Déplacement : message EWMH
  `_NET_MOVERESIZE_WINDOW` + XMoveWindow (`x11.move`) ; la mise en place (150 / 450 ms) passe après un
  déplacement trop rapproché (le test attend 0,7 s).
  `translate_coordinates` : (x, y) dans le PyGObject de Zorin, (ok, x, y) ailleurs → `_to_window`.
  `ui/x11.py` (libX11 par ctypes, sa propre connexion) : `_NET_WM_STATE_ABOVE` par ClientMessage à la
  racine, `XMoveWindow` en bas à droite de la zone utile (`GdkX11.X11Monitor.get_workarea`), après le
  redimensionnement (150 et 450 ms) ; hors X11, rien. Clic = pause ; glisser (> 8 px) = `begin_move` et
  pause du clic annulée ; double-clic / Échap = retour (taille, maximisée, file d'avant). Taille enregistrée
  à la fermeture : celle d'avant (`_pip_saved`). Testé : `image_dans_l_image` d'ui_smoke (openbox, xprop).
  `tools/screenshot.py --pip` capture l'écran réel.
- Temps restant / durée totale (clic sur la durée) : réglage `show_remaining`, retenu (0.1.8).
- `backend/mpris.py` (repris d'Ithaca, demandé par Tof) : `org.mpris.MediaPlayer2.nausicaa`. Le casque
  Bluetooth passe par là : BlueZ relaie l'AVRCP (double tape = PlayPause, écouteurs retirés = Pause) au
  lecteur MPRIS actif — Ithaca les recevait, Nausicaa 0.1.1 non. Pause / Stop = pause (vidéo gardée),
  Volume 0..1 = 0..100 %, Rate = vitesse de mpv, Fullscreen (racine) lu / changé par l'application.
  Démarré / arrêté en direct (`features` « mpris »), seulement avec un bus de session.
  Validé chez Tof avec son casque Bluetooth (0.1.2).
- Son (validé chez Tof, 0.1.6) : flux nommé « Nausicaa » (`audio_client_name`). Volume de Nausicaa = volume
  logiciel de mpv ; le volume du FLUX côté PipeWire (`ao-volume`, ce que montre pavucontrol) est remis à
  100 % à l'ouverture de la sortie son (`audio-out-params`), puis après 1 s et 2,5 s (PipeWire restaure tard).
  Avant : flux resté à 75 % (invisible), 120 % dans Nausicaa ≈ 75 % dans VLC (dont le curseur EST le volume
  du flux). mpv ne prévient pas des changements d'ao-volume : on le lit nous-mêmes. PIÈGE python-mpv :
  `mpv["nom"]` = OPTION (`options/nom`), `mpv.nom_souligné` = PROPRIÉTÉ — ao-volume n'est qu'une propriété
  (premier essai muet). `Player.set_property` passe par l'attribut. ao-volume est linéaire (75 % du
  mélangeur = 42,19). `NAUSICAA_DEBUG=1` : diagnostic sur la sortie d'erreur.
- Décodage matériel : sans `/proc/driver/nvidia`, `gpu_hwdec_interop=vaapi` — sinon mpv charge aussi
  l'interop CUDA à la création du contexte de rendu et écrit « Cannot load libcuda.so.1 » (vu chez Tof).
- `app.py` : instance unique, HANDLES_OPEN + ligne de commande (`--enqueue`), fermer = quitter,
  inhibition IDLE | SUSPEND pendant la lecture.

## Tests

```sh
python3.12 -m unittest discover -s tests            # moteur (vrai mpv, vo/ao factices)
dbus-run-session -- python3.12 -m unittest discover -s tests   # + MPRIS (vrai client D-Bus)
xvfb-run -a -s "-screen 0 1280x800x24" python3.12 tests/ui_smoke.py   # doit finir par « Aucune erreur. »
LANGUAGE=en … ; LANGUAGE=de …
python3 -m pyflakes nausicaa tests tools
```

ui_smoke lance openbox s'il existe (plein écran et réduction passent par le gestionnaire de fenêtres ;
sans lui, réduction simulée). Vidéos de test : `tests/media.py` (ffmpeg, x264 ultrafast).
Captures : `Gtk.WidgetPaintable` de la FENÊTRE peut rendre une image en retard (barre flottante
absente alors qu'elle est affichée) ; pour une capture fidèle, `import -window root` (ImageMagick)
sous Xvfb + openbox.

## Publication (comme Ithaca)

PPA `ppa:nourpon/nausicaa` (https://launchpad.net/~nourpon/+archive/ubuntu/nausicaa, créé par Tof) :
`tools/ppa.sh` chez Tof. Nouvelle version : version dans `nausicaa/__init__.py`, `debian/nausicaa.1`,
`<release>` de la fiche AppStream, entrée de `debian/changelog` (« X~noble1 »), commit « Version X »,
push sur main (le workflow `release-deb.yml` publie le .deb dans les Releases), puis Tof lance
`tools/ppa.sh`.

Site de présentation (`site/index.html`, anglais, tons orangés pour se distinguer d'Odysseus (bleu) et
d'Ithaca (violet), même gabarit) : https://tofmicro.pages-perso.free.fr/Nausicaa/, envoyé par FTP à la fin
de `tools/ppa.sh` via `tools/site.sh` (repris d'Ithaca : lftp, ~/.netrc de Tof, `--transfer-all`, jamais de
suppression ; `--essai` montre sans envoyer). Version dans la pastille et l'accroche (« v0.1.1 »,
« Version 0.1.1 ») : à tenir à jour. Captures de `site/img/` en anglais, sombre sauf `light.png` :
`LANGUAGE=en xvfb-run -a -s "-screen 0 1280x800x24" python3.12 tools/screenshot.py site/img/NOM.png [options]`
— dark (`--dark --playlist`), light (`--playlist`), fullscreen (`--dark --fullscreen --volume`, écran réel
1280×800), welcome (`--dark --welcome`), features (`--dark --prefs=features`) ; `icon.svg` = icône couleur.
Vidéos de démonstration : série « Phaeacia » (fractales de Mandelbrot), image arrêtée à 0:09 de l'épisode 2.
Captures du SITE (idée de Tof, plus vivant que des fractales) : `--source=` Big Buck Bunny 1080p
(download.blender.org/demo/movies/BBB/bbb_sunflower_1080p_30fps_normal.mp4.zip, © 2008 Blender Foundation,
CC BY 3.0 : crédit obligatoire, en bas du site et sous la capture principale), découpé en six extraits
(`BBB_CUTS`) — dark / light : brigands (épisode 2) ; fullscreen : écureuil volant (`--episode=6`) ; pip :
papillon (`--episode=3`) ; `--delay=6000` (découpage plus long). Pas le lapin de face, vu partout.
`site/img/pip.png` : `--dark --pip` (écran réel, commandes au survol) puis la petite fenêtre découpée (zone
non noire de la capture) ; posée sur un faux coin de bureau en CSS (`.desk`, section `#pip`).
Paquet vérifié ici : dpkg-buildpackage, lintian sans remarque, appstreamcli validate (note pédante).
