"""Nausicaa lecteur vidéo par défaut (Préférences → Général), un seul interrupteur, comme celui d'Odysseus :

- activé : l'application devient celle par défaut pour ses types (les vidéos ; l'audio et les listes de lecture restent à Ithaca, choix de Tof), dans
  ~/.config/mimeapps.list, comme `xdg-mime default` ; l'application d'avant est notée pour chaque type ;
- coupé : l'application d'avant revient (sinon les associations du système).

L'état affiché est celui du système (type principal `MAIN`), relu à l'ouverture des Préférences.
Leçon d'Odysseus (`set_default_app`) : les ALIAS d'un type déjà présents dans mimeapps.list (audio/x-m4a
pour audio/mp4…) gardaient l'ancienne application ; ils reçoivent le même choix, et les lignes de
« <bureau>-mimeapps.list », lu avant mimeapps.list, sont retirées pour ces types."""

from __future__ import annotations

import configparser
import os

from gi.repository import Gio, GLib

from . import APP_ID
from .settings import settings

DESKTOP = APP_ID + ".desktop"
TYPES = [
    "video/mp4", "video/x-matroska", "video/webm", "video/x-msvideo", "video/avi", "video/quicktime",
    "video/x-ms-wmv", "video/x-ms-asf", "video/x-flv", "video/mpeg", "video/mp2t", "video/x-m4v", "video/ogg",
    "video/3gpp", "video/3gpp2", "video/dv", "video/vnd.rn-realvideo", "video/x-ogm+ogg", "video/x-theora+ogg",
    "application/vnd.rn-realmedia",
]
MAIN = TYPES[0]
COMMAND = f"xdg-mime default {DESKTOP} " + " ".join(["video/mp4", "video/x-matroska", "…"])


def _app(desktop_id: str):
    if not desktop_id:
        return None
    try:
        return Gio.DesktopAppInfo.new(desktop_id)
    except (TypeError, GLib.Error):          # PyGObject : « constructor returned NULL »
        return None


def installed() -> bool:
    """Le lanceur (.desktop) est-il installé ? (pas lancé depuis les sources seulement)"""
    return _app(DESKTOP) is not None


def current(ctype: str) -> str:
    app = Gio.AppInfo.get_default_for_type(ctype, False)
    return app.get_id() if app is not None else ""


def is_default() -> bool:
    return current(MAIN) == DESKTOP


def set_default(on: bool) -> bool:
    """Fait (ou défait) de l'application celle par défaut pour ses types. True si c'est fait."""
    try:
        if on:
            app = _app(DESKTOP)
            if app is None:
                return False
            before = dict(settings["previous_defaults"])
            for t in TYPES:
                cur = current(t)
                if cur and cur != DESKTOP:
                    before[t] = cur
                set_default_app(app, t)
            settings["previous_defaults"] = before
        else:
            before = settings["previous_defaults"]
            for t in TYPES:
                other = _app(before.get(t, ""))
                if other is not None and other.get_id() != DESKTOP:
                    set_default_app(other, t)
                else:
                    Gio.AppInfo.reset_type_associations(t)
                    _forget_us(t)
            settings["previous_defaults"] = {}
    except GLib.Error as e:
        print(f"{APP_ID}: application par défaut :", e.message)
        return False
    return True


def _mimeapps_files() -> list:
    config = GLib.get_user_config_dir()
    names = ["mimeapps.list"] + [d.lower() + "-mimeapps.list"
                                 for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d]
    return [os.path.join(config, n) for n in names]


def set_default_app(app: Gio.AppInfo, ctype: str):
    """`app` par défaut pour `ctype`, alias compris (repris d'Odysseus)."""
    app.set_as_default_for_type(ctype)
    files = _mimeapps_files()
    aliases = set()
    for path in files:
        parser = configparser.RawConfigParser(strict=False, delimiters=("=",), interpolation=None)
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError):
            continue
        if parser.has_section("Default Applications"):
            aliases.update(k for k in parser.options("Default Applications")
                           if k != ctype and Gio.content_type_equals(k, ctype))
    for alias in sorted(aliases):
        try:
            app.set_as_default_for_type(alias)
        except GLib.Error:
            pass
    for path in files[1:]:
        _drop_default_lines(path, ctype)


def _forget_us(ctype: str):
    """Coupé sans application d'avant : nos lignes pour ce type et ses alias retirées partout."""
    for path in _mimeapps_files():
        _drop_default_lines(path, ctype, only=DESKTOP)


def _drop_default_lines(path: str, ctype: str, only: str = ""):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return
    out, section, changed = [], "", False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
        elif section == "Default Applications" and "=" in stripped and not stripped.startswith("#"):
            key, value = (x.strip() for x in stripped.split("=", 1))
            if (key == ctype or Gio.content_type_equals(key, ctype)) and (not only or value.split(";")[0] == only):
                changed = True
                continue
        out.append(line)
    if changed:
        tmp = path + ".tmp-defaultapp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(out)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
