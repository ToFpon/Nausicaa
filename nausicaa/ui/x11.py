"""Ce que GTK 4 ne sait plus faire pour une fenêtre, fait directement en X11 (libX11 par ctypes) :
la garder au-dessus des autres (_NET_WM_STATE_ABOVE) et la placer (XMoveWindow).

Sert à l'image dans l'image. Sous Wayland, une application n'a pas ce droit : ces fonctions ne font
rien (renvoient False), l'utilisateur peut cocher « Toujours au-dessus » dans le menu de la fenêtre.
Une connexion X à nous (XOpenDisplay sur $DISPLAY) : les messages au gestionnaire de fenêtres
passent par la fenêtre racine, l'identifiant de NOTRE fenêtre vient de GdkX11.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from typing import Optional

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

CLIENT_MESSAGE = 33
SUBSTRUCTURE_REDIRECT = 1 << 20
SUBSTRUCTURE_NOTIFY = 1 << 19
NET_WM_STATE_REMOVE, NET_WM_STATE_ADD = 0, 1


class _ClientMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("l", ctypes.c_long * 5)]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _ClientMessage), ("pad", ctypes.c_long * 24)]


_lib = None
_dpy = None


def _display():
    """Notre connexion X (ouverte une fois), None hors X11."""
    global _lib, _dpy
    if _dpy is not None:
        return _dpy
    if not os.environ.get("DISPLAY") or not type(Gdk.Display.get_default()).__name__.startswith("X11"):
        return None
    try:
        _lib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    except OSError:
        return None
    _lib.XOpenDisplay.restype = ctypes.c_void_p
    _lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    _lib.XInternAtom.restype = ctypes.c_ulong
    _lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    _lib.XDefaultRootWindow.restype = ctypes.c_ulong
    _lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    _lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                                ctypes.POINTER(_XEvent)]
    _lib.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
    _lib.XFlush.argtypes = [ctypes.c_void_p]
    dpy = _lib.XOpenDisplay(None)
    _dpy = dpy or None
    return _dpy


def xid(window) -> Optional[int]:
    """Identifiant X11 d'une Gtk.Window réalisée, None hors X11."""
    surface = window.get_surface()
    if surface is None:
        return None
    try:
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
    except (ValueError, ImportError):
        return None
    if not isinstance(surface, GdkX11.X11Surface):
        return None
    return surface.get_xid()


def set_above(window, above: bool) -> bool:
    """Garder la fenêtre au-dessus des autres (ou plus), demandé au gestionnaire de fenêtres."""
    dpy, wid = _display(), xid(window)
    if dpy is None or wid is None:
        return False
    ev = _XEvent()
    ev.xclient.type = CLIENT_MESSAGE
    ev.xclient.send_event = 1
    ev.xclient.window = wid
    ev.xclient.message_type = _lib.XInternAtom(dpy, b"_NET_WM_STATE", 0)
    ev.xclient.format = 32
    ev.xclient.l[0] = NET_WM_STATE_ADD if above else NET_WM_STATE_REMOVE
    ev.xclient.l[1] = _lib.XInternAtom(dpy, b"_NET_WM_STATE_ABOVE", 0)
    ev.xclient.l[2] = 0
    ev.xclient.l[3] = 1                                   # source : une application
    _lib.XSendEvent(dpy, _lib.XDefaultRootWindow(dpy), 0, SUBSTRUCTURE_REDIRECT | SUBSTRUCTURE_NOTIFY,
                    ctypes.byref(ev))
    _lib.XFlush(dpy)
    return True


def move(window, x: int, y: int) -> bool:
    """Placer la fenêtre (coordonnées X11, en pixels de l'écran)."""
    dpy, wid = _display(), xid(window)
    if dpy is None or wid is None:
        return False
    _lib.XMoveWindow(dpy, wid, int(x), int(y))
    _lib.XFlush(dpy)
    return True


def workarea(window) -> Optional[tuple]:
    """Zone utile de l'écran de la fenêtre (sans tableau de bord ni dock), en pixels X11."""
    surface = window.get_surface()
    display = window.get_display()
    if surface is None or display is None:
        return None
    monitor = display.get_monitor_at_surface(surface)
    if monitor is None:
        return None
    rect = None
    try:
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
        if isinstance(monitor, GdkX11.X11Monitor):
            rect = GdkX11.X11Monitor.get_workarea(monitor)
    except (ValueError, ImportError, AttributeError, TypeError):
        rect = None
    if rect is None:
        rect = monitor.get_geometry()
    scale = monitor.get_scale_factor() or 1
    return rect.x * scale, rect.y * scale, rect.width * scale, rect.height * scale
