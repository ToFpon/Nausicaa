"""Zone vidéo : mpv dessine dans un Gtk.GLArea par son API de rendu OpenGL (comme Celluloid).

La vidéo est un widget GTK ordinaire : on peut poser des contrôles par-dessus (Gtk.Overlay), la mettre
en plein écran, la redimensionner — sous X11 comme sous Wayland, sans identifiant de fenêtre native.

Fonctions OpenGL : mpv les demande par leur nom (get_proc_address). GTK 4.14 peut utiliser EGL ou GLX
sous X11 (EGL sous Wayland) : on interroge le bon (_proc_address).
"""

from __future__ import annotations

import ctypes
import ctypes.util
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

import mpv  # noqa: E402

GL_FRAMEBUFFER_BINDING = 0x8CA6
GL_RENDERER = 0x1F01
SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swrast", "software")


def _lib(*names):
    for n in names:
        try:
            return ctypes.CDLL(n)
        except OSError:
            continue
    return None


_egl = _lib("libEGL.so.1", "libEGL.so")
_glx = _lib("libGL.so.1", "libGL.so")
_gl = _lib("libOpenGL.so.0", "libGL.so.1", "libGL.so")
if _gl is not None:
    _gl.glGetString.restype = ctypes.c_char_p
    _gl.glGetString.argtypes = [ctypes.c_uint]
for lib, fn in ((_egl, "eglGetProcAddress"), (_glx, "glXGetProcAddressARB")):
    if lib is not None and hasattr(lib, fn):
        getattr(lib, fn).restype = ctypes.c_void_p
        getattr(lib, fn).argtypes = [ctypes.c_char_p]


def _uses_glx() -> bool:
    """GTK sous X11 avec GLX (pas d'EGL) : les fonctions OpenGL se demandent alors à GLX."""
    display = Gdk.Display.get_default()
    try:
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
    except (ValueError, ImportError):
        return False
    if isinstance(display, GdkX11.X11Display):
        try:
            return display.get_egl_display() is None
        except Exception:  # noqa: BLE001 — ancienne GdkX11 : EGL par défaut depuis GTK 4.x
            return False
    return False


def _proc_address_fn() -> Callable:
    use_glx = _uses_glx()

    def get(_ctx, name):
        if use_glx and _glx is not None:
            return _glx.glXGetProcAddressARB(name) or 0
        if _egl is not None:
            return _egl.eglGetProcAddress(name) or 0
        return 0
    return get


class VideoView(Gtk.GLArea):
    """Affiche la vidéo d'un mpv.MPV. `player` doit être créé avec vo="libmpv"."""

    def __init__(self, player: "mpv.MPV"):
        super().__init__(hexpand=True, vexpand=True, auto_render=False, has_depth_buffer=False)
        self.player = player
        self.ctx: Optional[mpv.MpvRenderContext] = None
        self._pending = False
        self._proc = None
        self._waiting: list = []
        self.gl_error: Optional[str] = None
        self.renderer = ""
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
        self.connect("render", self._on_render)

    # -- contexte de rendu -------------------------------------------------------------------
    def when_ready(self, cb: Callable):
        """cb() une fois le contexte de rendu créé : mpv refuse d'ouvrir une vidéo avant (vo=libmpv
        sans contexte → le fichier « ne peut pas être lu »). Tout de suite s'il existe déjà."""
        if self.ctx is not None or self.gl_error is not None:
            cb()
        else:
            self._waiting.append(cb)

    def _on_realize(self, _area):
        self.make_current()
        err = self.get_error()
        if err is not None:
            self.gl_error = err.message
        else:
            self.renderer = (_gl.glGetString(GL_RENDERER) or b"").decode("utf-8", "replace") if _gl else ""
            if any(w in self.renderer.lower() for w in SOFTWARE_RENDERERS):
                # rendu logiciel (machine virtuelle, pilote absent) : les filtres de mpv par défaut y
                # dessinent une grille de rayures noires ; les bilinéaires sont justes (et légers)
                for opt in ("scale", "dscale", "cscale"):
                    self.player[opt] = "bilinear"
            self._proc = mpv.MpvGlGetProcAddressFn(_proc_address_fn())   # gardée : appelée par mpv
            try:
                self.ctx = mpv.MpvRenderContext(self.player, "opengl",
                                                opengl_init_params={"get_proc_address": self._proc})
                self.ctx.update_cb = self._on_update
            except Exception as e:  # noqa: BLE001 — pilote sans OpenGL utilisable : on le dira
                self.gl_error = str(e)
        waiting, self._waiting = self._waiting, []
        for cb in waiting:
            cb()

    def _on_unrealize(self, _area):
        if self.ctx is not None:
            self.make_current()
            self.ctx.free()
            self.ctx = None

    def _on_update(self):
        """Appelé par mpv depuis SON fil : une image est prête. Redessin demandé au fil de GTK, une fois."""
        if not self._pending:
            self._pending = True
            GLib.idle_add(self._queue)

    def _queue(self):
        self._pending = False
        if self.ctx is not None and self.get_realized():
            self.queue_render()
        return False

    def _on_render(self, _area, _gl_context):
        self._renders = getattr(self, "_renders", 0) + 1         # (diagnostic des tests)
        if self.ctx is None:
            return True
        fbo = ctypes.c_int(0)
        if _gl is not None:
            _gl.glGetIntegerv(GL_FRAMEBUFFER_BINDING, ctypes.byref(fbo))   # le tampon de GTK
        scale = self.get_scale_factor()
        w, h = self.get_width() * scale, self.get_height() * scale
        self.ctx.render(flip_y=True, opengl_fbo={"w": w, "h": h, "fbo": fbo.value})
        return True
