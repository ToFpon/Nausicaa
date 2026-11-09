"""Panneau de la file de lecture (à droite de la vidéo).

Gtk.ListBox : une file de vidéos reste courte (quelques centaines au plus). Double-clic / Entrée :
lire ; Suppr : retirer ; glisser une ligne : la déplacer ; déposer des fichiers : les ajouter à la
fin. En bas : mélanger, répéter, ajouter, enregistrer la file en liste de lecture, vider.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GObject, Gtk  # noqa: E402

from ..backend import playlists  # noqa: E402
from ..format import duration as fmt, long_duration  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from .common import choose_files, choose_save, error, popup_menu, shown, show_in_folder, toast  # noqa: E402

REPEAT_ICONS = {"consecutive": "media-playlist-consecutive-symbolic", "all": "media-playlist-repeat-symbolic",
                "one": "media-playlist-repeat-song-symbolic"}


def repeat_label(mode: str) -> str:
    return {"consecutive": tr("Sans répétition"), "all": tr("Répéter la file"),
            "one": tr("Répéter la vidéo")}[mode]


class Row(Gtk.ListBoxRow):
    def __init__(self, item):
        super().__init__()
        self.item = item
        box = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self.icon = Gtk.Image(icon_name="media-playback-start-symbolic", opacity=0)
        box.append(self.icon)
        self.title = Gtk.Label(xalign=0, hexpand=True, ellipsize=3, tooltip_text=shown(item.path))
        box.append(self.title)
        self.length = Gtk.Label(xalign=1)
        self.length.add_css_class("dim-label")
        self.length.add_css_class("numeric")
        box.append(self.length)
        self.set_child(box)
        self.refresh()

    def refresh(self):
        self.title.set_label(shown(self.item.display_title))
        self.length.set_label(fmt(self.item.duration) if self.item.duration else "")

    def set_current(self, current: bool):
        self.icon.set_opacity(1 if current else 0)
        if current:
            self.add_css_class("current")
        else:
            self.remove_css_class("current")


class PlaylistPanel(Gtk.Box):
    def __init__(self, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, width_request=300)
        self.add_css_class("playlist-panel")
        self.controller = controller
        header = Adw.HeaderBar()           # boutons de la fenêtre : libadwaita les met au bord droit
        self.heading = Adw.WindowTitle(title=tr("File de lecture"))
        header.set_title_widget(self.heading)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=tr("Ajouter des vidéos à la file"))
        add.connect("clicked", self._add_files)
        header.pack_start(add)
        menu = Gio.Menu()
        menu.append(tr("Enregistrer la file…"), "win.save-playlist")
        menu.append(tr("Vider la file"), "win.clear-queue")
        header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                                       tooltip_text=tr("Menu de la file")))
        self.append(header)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE, activate_on_single_click=False)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", lambda _l, row: controller.play_item(row.item, from_start=False))
        self.empty = Adw.StatusPage(icon_name="view-list-symbolic", title=tr("File vide"),
                                    description=tr("Déposez des vidéos ici pour les ajouter"))
        self.empty.add_css_class("compact")
        self.stack = Gtk.Stack(vexpand=True)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.list)
        self.stack.add_named(scroller, "list")
        self.stack.add_named(self.empty, "empty")
        self.append(self.stack)

        bar = Gtk.Box(spacing=4, margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
        self.shuffle = Gtk.ToggleButton(icon_name="media-playlist-shuffle-symbolic", tooltip_text=tr("Mélanger"))
        self.shuffle.add_css_class("flat")
        self._shuffle_handler = self.shuffle.connect("toggled", lambda b: controller.set_shuffled(b.get_active()))
        bar.append(self.shuffle)
        self.repeat = Gtk.Button(icon_name=REPEAT_ICONS["consecutive"])
        self.repeat.add_css_class("flat")
        self.repeat.connect("clicked", lambda *_: controller.cycle_repeat())
        bar.append(self.repeat)
        self.summary = Gtk.Label(hexpand=True, xalign=1, ellipsize=3)
        self.summary.add_css_class("dim-label")
        self.summary.add_css_class("caption")
        bar.append(self.summary)
        self.append(bar)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.list.add_controller(keys)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_right_click)
        self.list.add_controller(click)
        drop = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        drop.set_gtypes([Gdk.FileList, GObject.TYPE_STRING])
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)
        self._rows: list[Row] = []
        self._drag_item = None
        controller.connect(self._on_event)
        self.rebuild()
        self.sync_modes()

    # -- contenu ------------------------------------------------------------------------------
    def rebuild(self):
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        self._rows = []
        for item in self.controller.queue.items:
            row = Row(item)
            self._attach_drag(row)
            self.list.append(row)
            self._rows.append(row)
        self.stack.set_visible_child_name("list" if self._rows else "empty")
        self.sync_current()
        self.sync_summary()

    def sync_current(self):
        current = self.controller.item
        for row in self._rows:
            row.set_current(row.item is current)
        n = len(self._rows)
        pos = self.controller.queue.pos
        self.heading.set_subtitle(f"{pos + 1} / {n}" if n and pos >= 0 else "")

    def sync_summary(self):
        n = len(self._rows)
        # durée totale seulement si toutes sont connues (une vidéo n'est mesurée qu'une fois lue)
        total = sum(r.item.duration for r in self._rows) if all(r.item.duration for r in self._rows) else 0
        text = ntr("{0} vidéo", "{0} vidéos", n).format(n)
        if total >= 60:
            text += " · " + long_duration(total)
        self.summary.set_label(text if n else "")

    def sync_modes(self):
        q = self.controller.queue
        self.shuffle.handler_block(self._shuffle_handler)
        self.shuffle.set_active(q.shuffled)
        self.shuffle.handler_unblock(self._shuffle_handler)
        self.repeat.set_icon_name(REPEAT_ICONS[q.repeat])
        self.repeat.set_tooltip_text(repeat_label(q.repeat))

    def _on_event(self, event, *args):
        if event == "queue":
            self.rebuild()
        elif event == "item":
            self.sync_current()
        elif event == "duration":
            for row in self._rows:
                if row.item is self.controller.item:
                    row.refresh()
            self.sync_summary()
        elif event == "modes":
            self.sync_modes()

    def selected_items(self) -> list:
        return [r.item for r in self.list.get_selected_rows()]

    # -- gestes -------------------------------------------------------------------------------
    def _on_key(self, _ctl, keyval, _code, _state):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            items = self.selected_items()
            if items:
                self.controller.remove(items)
            return True
        return False

    def _on_right_click(self, gesture, _n, x, y):
        row = self.list.get_row_at_y(int(y))
        if row is None:
            return
        if not row.is_selected():
            self.list.unselect_all()
            self.list.select_row(row)
        self._menu_items = self.selected_items()
        menu = Gio.Menu()
        menu.append(tr("Lire"), "queue.play")
        menu.append(tr("Retirer de la file"), "queue.remove")
        if len(self._menu_items) == 1 and not self._menu_items[0].stream:
            menu.append(tr("Afficher dans le dossier"), "queue.show")
        group = Gio.SimpleActionGroup()
        for name, cb in (("play", lambda: self.controller.play_item(self._menu_items[0])),
                         ("remove", lambda: self.controller.remove(self._menu_items)),
                         ("show", lambda: show_in_folder(self, self._menu_items[0].path))):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, cb=cb: cb())
            group.add_action(a)
        self.list.insert_action_group("queue", group)
        popup_menu(self.list, menu, x, y)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _attach_drag(self, row: Row):
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", self._on_drag_prepare)
        src.connect("drag-end", lambda *_: setattr(self, "_drag_item", None))
        row.add_controller(src)

    def _on_drag_prepare(self, source, _x, _y):
        row = source.get_widget()
        self._drag_item = row.item
        source.set_icon(Gtk.WidgetPaintable.new(row), 0, 0)
        return Gdk.ContentProvider.new_for_value("nausicaa-row")

    def _on_drop(self, _target, value, _x, y):
        if isinstance(value, str):
            if value != "nausicaa-row" or self._drag_item is None:
                return False
            # point visé, en coordonnées de la liste
            ok, _px, ly = self.translate_coordinates(self.list, 0, y)
            row = self.list.get_row_at_y(int(ly)) if ok else None
            index = row.get_index() if row is not None else len(self._rows) - 1
            item, self._drag_item = self._drag_item, None
            self.controller.move(item, index)
            return True
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if not paths:
            return False
        self.controller.open_paths(paths, replace=False, play=False,
                                   on_done=lambda n: toast(self, ntr("{0} vidéo ajoutée à la file",
                                                                     "{0} vidéos ajoutées à la file", n).format(n)))
        return True

    def _add_files(self, *_):
        folder = os.path.dirname(self.controller.item.path) if self.controller.item and \
            not self.controller.item.stream else os.path.expanduser("~")
        choose_files(self, tr("Ajouter des vidéos à la file"), folder,
                     lambda paths: self.controller.open_paths(paths, replace=False, play=False))

    # -- enregistrement -----------------------------------------------------------------------
    def save_queue(self):
        items = list(self.controller.queue.items)
        if not items:
            return
        first = next((i for i in items if not i.stream), None)
        folder = os.path.dirname(first.path) if first else os.path.expanduser("~")
        name = os.path.basename(folder) if first else tr("Liste de lecture")

        def chosen(path):
            try:
                written = playlists.save(items, path)
            except OSError as e:
                error(self, tr("Liste de lecture non enregistrée"), shown(e.strerror or str(e)))
                return
            toast(self, tr("Liste enregistrée : {0}").format(shown(os.path.basename(written))))
        choose_save(self, tr("Enregistrer la file"), os.path.join(folder, name + ".m3u"), chosen)
