#!/bin/sh
# Installation de Nausicaa (lecteur vidéo) pour l'utilisateur courant (~/.local), sans droits root.
#   ./install.sh             installe
#   ./install.sh --uninstall désinstalle (la file, les positions de reprise et les réglages restent)
set -e

PREFIX="${PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share"
APPDIR="$PREFIX/lib/nausicaa"        # le programme ; les données de l'utilisateur restent dans $SHARE/nausicaa
SRC="$(cd "$(dirname "$0")" && pwd)"
APP_ID="io.github.nausicaa.Nausicaa"
DESKTOP="$APP_ID.desktop"
SERVICE="$APP_ID.service"

if [ "$1" = "--uninstall" ]; then
    rm -rf "$APPDIR" "$PREFIX/bin/nausicaa" "$SHARE/applications/$DESKTOP" \
        "$SHARE/dbus-1/services/$SERVICE" \
        "$SHARE/icons/hicolor/scalable/apps/$APP_ID.svg" \
        "$SHARE/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg"
    update-desktop-database "$SHARE/applications" 2>/dev/null || true
    if [ -f "$SHARE/icons/hicolor/icon-theme.cache" ]; then
        gtk-update-icon-cache -f -t "$SHARE/icons/hicolor" 2>/dev/null || rm -f "$SHARE/icons/hicolor/icon-theme.cache"
    fi
    echo "Nausicaa a été désinstallé."
    exit 0
fi

if [ -d /usr/share/nausicaa ]; then
    echo "Attention : le paquet nausicaa (PPA) est déjà installé. Cette installation dans ~/.local"
    echo "passera devant lui ; pour revenir au paquet : ./install.sh --uninstall"
fi

missing=""
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Adw; assert Adw.MINOR_VERSION >= 5" 2>/dev/null \
    || missing="$missing python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
python3 -c "import mpv" 2>/dev/null || missing="$missing python3-mpv libmpv2"
if [ -n "$missing" ]; then
    echo "Dépendances manquantes. Debian/Ubuntu/Zorin : sudo apt install$missing"
    exit 1
fi

mkdir -p "$APPDIR" "$PREFIX/bin" "$SHARE/applications" "$SHARE/icons/hicolor/scalable/apps" \
    "$SHARE/icons/hicolor/symbolic/apps" "$SHARE/dbus-1/services"
rm -rf "$APPDIR/nausicaa" "$APPDIR/data"
cp -r "$SRC/nausicaa" "$SRC/data" "$APPDIR/"
find "$APPDIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
cat > "$PREFIX/bin/nausicaa" <<LAUNCH
#!/bin/sh
# -P : pas de dossier courant dans les chemins d'import (un dossier supprimé faisait planter le
# démarrage, et un « gi.py » posé dans le dossier courant aurait été exécuté)
exec python3 -P -c 'import sys; sys.path.insert(0, "$APPDIR"); from nausicaa.app import main; sys.exit(main(sys.argv))' "\$@"
LAUNCH
chmod +x "$PREFIX/bin/nausicaa"
sed "s|^Exec=nausicaa|Exec=$PREFIX/bin/nausicaa|" "$SRC/data/$DESKTOP" > "$SHARE/applications/$DESKTOP"
# DBusActivatable=true : GIO (dock, gio open, autres applications) demande l'ouverture au bus de
# session, qui lance Nausicaa avec l'environnement de la session graphique
cat > "$SHARE/dbus-1/services/$SERVICE" <<SERVICE
[D-BUS Service]
Name=$APP_ID
Exec=$PREFIX/bin/nausicaa --gapplication-service
SERVICE
cp "$SRC/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$SHARE/icons/hicolor/scalable/apps/"
cp "$SRC/data/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg" "$SHARE/icons/hicolor/symbolic/apps/"
update-desktop-database "$SHARE/applications" 2>/dev/null || true
gtk-update-icon-cache -f -t "$SHARE/icons/hicolor" 2>/dev/null || true

echo "Nausicaa est installé. Lancez-le depuis le menu des applications ou avec : nausicaa"
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "Astuce : ajoutez $PREFIX/bin à votre PATH." ;; esac
