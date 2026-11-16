"""Interrupteur « application par défaut » (defaultapp.py) sur de vrais fichiers mimeapps.list : activé,
l'application prend ses types et l'application d'avant est notée ; coupé, celle-ci revient (sinon les
associations du système). Chaque cas tourne dans un processus à part : GIO garde en mémoire les dossiers
d'applications (XDG_DATA_HOME) au premier usage, déjà fixés par les autres tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "nausicaa"

CHILD = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from gi.repository import Gio
from nausicaa import defaultapp as d
out = {"installed": d.installed()}
other = Gio.DesktopAppInfo.new("org.example.Other.desktop")
if sys.argv[2] == "with-previous":
    for t in d.TYPES:
        other.set_as_default_for_type(t)
out["before"] = d.current(d.MAIN)
out["on"] = d.set_default(True)
out["after_on"] = {t: d.current(t) for t in d.TYPES}
out["is_default"] = d.is_default()
from nausicaa.settings import settings
out["remembered"] = dict(settings["previous_defaults"])
out["off"] = d.set_default(False)
out["after_off"] = {t: d.current(t) for t in d.TYPES}
out["is_default_off"] = d.is_default()
print(json.dumps(out))
'''


def run(mode: str) -> dict:
    tmp = tempfile.mkdtemp(prefix="defaultapp-")
    apps = os.path.join(tmp, "data", "applications")
    os.makedirs(apps)
    sys.path.insert(0, ROOT)
    desktop = __import__(PKG).APP_ID + ".desktop"
    with open(os.path.join(ROOT, "data", desktop), encoding="utf-8") as fh:
        text = fh.read()
    mime = next(line for line in text.splitlines(True) if line.startswith("MimeType="))
    # (GIO ignore un lanceur dont la commande est introuvable : celle de l'application n'est pas installée ici)
    with open(os.path.join(apps, desktop), "w", encoding="utf-8") as fh:
        fh.write("".join("Exec=true %U\n" if line.startswith("Exec=") else line for line in text.splitlines(True)))
    with open(os.path.join(apps, "org.example.Other.desktop"), "w", encoding="utf-8") as fh:
        fh.write("[Desktop Entry]\nType=Application\nName=Autre\nExec=true %U\n" + mime)
    env = dict(os.environ, XDG_DATA_HOME=os.path.join(tmp, "data"), XDG_CONFIG_HOME=os.path.join(tmp, "config"),
               XDG_CACHE_HOME=os.path.join(tmp, "cache"), XDG_CURRENT_DESKTOP="GNOME")
    try:
        r = subprocess.run([sys.executable, "-c", CHILD, ROOT, mode], env=env, capture_output=True, text=True,
                           timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class DefaultAppTest(unittest.TestCase):
    def test_on_then_previous_comes_back(self):
        out = run("with-previous")
        desktop = __import__(PKG).APP_ID + ".desktop"
        self.assertTrue(out["installed"])
        self.assertEqual(out["before"], "org.example.Other.desktop")
        self.assertTrue(out["on"] and out["is_default"])
        self.assertTrue(all(v == desktop for v in out["after_on"].values()), out["after_on"])
        self.assertTrue(all(v == "org.example.Other.desktop" for v in out["remembered"].values()), out["remembered"])
        self.assertTrue(out["off"] and not out["is_default_off"])
        self.assertTrue(all(v == "org.example.Other.desktop" for v in out["after_off"].values()),
                        "coupé : l'application d'avant revient " + str(out["after_off"]))

    def test_off_without_previous(self):
        out = run("fresh")
        desktop = __import__(PKG).APP_ID + ".desktop"
        self.assertTrue(out["on"] and out["is_default"])
        self.assertTrue(out["off"])
        self.assertTrue(all(v != desktop for v in out["after_off"].values()),
                        "coupé sans application d'avant : la main est rendue " + str(out["after_off"]))


if __name__ == "__main__":
    unittest.main()
