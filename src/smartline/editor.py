"""RouteEditor - select a finished connector and reshape it.

    editor = RouteEditor(scene)
    editor.routeEdited.connect(lambda item, old, new: undo_stack.push(...))
    editor.set_active(True)          # follows the scene selection by default

The editor is the interactive half of :mod:`smartline.edit`, the same way
``SmartLineTool`` is the interactive half of the routers.  It is an event filter
on the scene (no subclassing), it only swallows clicks that land on one of its
handles, and it never touches your item directly: every change goes through
``editor.apply(item, route)``, which you can replace.

What the user sees on the selected connector
    squares     anchors (vertices)                drag to move
    bars        the middle of a straight section  drag to move the whole section
    circles     cubic / quadratic control points  drag to bend; a thin *tangent
                line* joins each one to its anchor
Gestures
    double-click the line      insert an anchor there
    double-click an anchor     delete it            (also Delete / Backspace)
    click a line END           select it: the last section - and the curve attached to it -
                               turns red; Delete / Backspace trims it off (repeat to eat back)
    C / L                      make the section under the cursor a cubic / a line
    Shift  while dragging      free move (do not keep the route orthogonal)
    Alt    while dragging      break the tangent (cusp);  Ctrl = mirrored lengths
    Esc                        abandon the drag in progress
"""
from __future__ import annotations

from typing import Callable, List, Optional

from . import edit
from . import geometry as g
from .qt_compat import Qt, QtCore, QtGui, QtWidgets, Signal, enum
from .route import Route
from .tool import _has, _iv, route_to_path

QPointF = QtCore.QPointF
_T = lambda n: enum(QtCore.QEvent, "Type", n)                 # noqa: E731
_EV_PRESS, _EV_MOVE = _T("GraphicsSceneMousePress"), _T("GraphicsSceneMouseMove")
_EV_RELEASE, _EV_DBL = _T("GraphicsSceneMouseRelease"), _T("GraphicsSceneMouseDoubleClick")
_EV_KEY = _T("KeyPress")
_LEFT = enum(Qt, "MouseButton", "LeftButton")
_NO_BUTTON = enum(Qt, "MouseButton", "NoButton")
_SHIFT = enum(Qt, "KeyboardModifier", "ShiftModifier")
_ALT = enum(Qt, "KeyboardModifier", "AltModifier")
_CTRL = enum(Qt, "KeyboardModifier", "ControlModifier")
_K = lambda n: _iv(enum(Qt, "Key", n))                        # noqa: E731


class _Overlay(QtWidgets.QGraphicsItem):
    """Paints the handles at a constant on-screen size.  It has an empty shape,
    so it never intercepts clicks meant for the items underneath."""

    def __init__(self, editor: "RouteEditor"):
        super().__init__()
        self._ed = editor
        self._rect = QtCore.QRectF()
        self.setZValue(1e9 + 1)
        self.setAcceptedMouseButtons(_NO_BUTTON)

    def set_rect(self, rect) -> None:
        self.prepareGeometryChange()
        self._rect = rect

    def boundingRect(self):                                   # noqa: N802
        return self._rect

    def shape(self):
        return QtGui.QPainterPath()

    def paint(self, painter, option, widget=None):
        ed = self._ed
        scale = abs(painter.worldTransform().m11()) or 1.0
        r = ed.handle_px / scale
        accent, hot_c = QtGui.QColor(ed.color), QtGui.QColor(ed.hot_color)
        line = QtGui.QPen(accent, 0, enum(Qt, "PenStyle", "DashLine"))
        outline = QtGui.QPen(accent, 0)
        white = QtGui.QBrush(QtGui.QColor(255, 255, 255))
        painter.setPen(line)
        for h in ed._handles:                                 # tangent lines first, under the knobs
            if h.is_control and h.anchor is not None:
                painter.drawLine(QPointF(*h.anchor), QPointF(*h.pos))
        doomed = ed.end_selection_points()              # what Delete would remove from the end
        if len(doomed) >= 2:
            red = QtGui.QPen(QtGui.QColor(ed.delete_color), 0)
            red.setWidthF(4.0)
            red.setCosmetic(True)
            painter.setPen(red)
            for k in range(len(doomed) - 1):
                painter.drawLine(QPointF(*doomed[k]), QPointF(*doomed[k + 1]))
        painter.setPen(outline)
        for h in ed._handles:
            hot = ed._hot is not None and (h.kind, h.index) == (ed._hot.kind, ed._hot.index)
            painter.setBrush(QtGui.QBrush(hot_c) if hot else white)
            x, y = h.pos
            if h.is_control:
                painter.drawEllipse(QPointF(x, y), r, r)
            elif h.kind == "anchor":
                painter.drawRect(QtCore.QRectF(x - r, y - r, 2 * r, 2 * r))
            else:                                             # section grip: a small bar
                painter.drawRect(QtCore.QRectF(x - r * 0.7, y - r * 0.7, r * 1.4, r * 1.4))


class RouteEditor(QtCore.QObject):
    editingStarted = Signal(object)            # item
    editingStopped = Signal(object)            # item
    routeChanged = Signal(object, object)      # item, Route   - live, on every drag step
    routeEdited = Signal(object, object, object)   # item, old Route, new Route - one per gesture
    removalRequested = Signal(object)          # item - trimming would leave nothing: delete the line

    def __init__(self, scene, parent=None):
        super().__init__(parent if parent is not None else scene)
        self.scene = scene
        self.follow_selection = True     # edit whatever single connector is selected
        self.handle_px = 4.5             # half-size of a handle, in screen pixels
        self.hit_px = 9.0                # grab radius, in screen pixels
        self.grid = 0.0
        self.corner_radius = 0.0         # used by the default ``apply`` for polylines
        self.keep_orthogonal = True
        self.link = "aligned"            # default tangent link: aligned | mirrored | free
        self.show_section_grips = True
        self.color, self.hot_color, self.delete_color = "#1f6fd0", "#ffb000", "#e5484d"
        #: ``f(QPointF) -> QPointF | None`` - ports; applied to route *ends* only
        self.snap_provider: Optional[Callable] = None
        #: ``f(item) -> Route | None`` - where an item keeps its route
        self.route_of: Callable = lambda item: getattr(item, "_smartline_route", None)
        #: ``f(item, Route)`` - push an edited route back into the item
        self.apply: Optional[Callable] = None

        self._active = False
        self._item = None
        self._route: Optional[Route] = None
        self._handles: List[edit.Handle] = []
        self._hot: Optional[edit.Handle] = None
        self._drag = None                # (handle, base route, grab point)
        self._end: Optional[str] = None  # "start" / "end": the line end selected for trimming
        self._cursor: g.Pt = (0.0, 0.0)
        self._overlay = _Overlay(self)
        self._overlay.setVisible(False)
        scene.addItem(self._overlay)

    # ------------------------------------------------------------- public API
    def set_active(self, on: bool) -> None:
        if on == self._active:
            return
        self._active = on
        if on:
            self.scene.installEventFilter(self)
            self.scene.selectionChanged.connect(self._on_selection)
            for v in self.scene.views():
                v.viewport().setMouseTracking(True)
            self._on_selection()
        else:
            self.stop()
            self.scene.removeEventFilter(self)
            try:
                self.scene.selectionChanged.disconnect(self._on_selection)
            except (TypeError, RuntimeError):
                pass

    def is_active(self) -> bool:
        return self._active

    def is_editing(self) -> bool:
        return self._item is not None

    def item(self):
        return self._item

    def route(self) -> Optional[Route]:
        return self._route

    def edit(self, item, route: Optional[Route] = None) -> bool:
        """Start editing *item*.  Returns False if it carries no route."""
        route = route if route is not None else self.route_of(item)
        if route is None:
            return False
        if self._item is not None and self._item is not item:
            self.stop()
        self._item, self._route, self._drag = item, route, None
        self._refresh()
        self.editingStarted.emit(item)
        return True

    def stop(self) -> None:
        item, self._item, self._route = self._item, None, None
        self._handles, self._hot, self._drag, self._end = [], None, None, None
        self._overlay.setVisible(False)
        if item is not None:
            self.editingStopped.emit(item)

    def refresh(self) -> None:
        """Re-read the edited item's route after something else changed it
        (a re-route, an undo ...), so the handles follow."""
        if self._item is not None:
            r = self.route_of(self._item)
            if r is not None:
                self._route, self._drag = r, None
                self._refresh()

    def set_route(self, route: Route, record: bool = True) -> None:
        """Replace the edited item's route programmatically (one undoable step)."""
        if self._item is None:
            return
        old, self._route = self._route, route
        self._push(route)
        if record:
            self.routeEdited.emit(self._item, old, route)

    # ---- trimming from an end -------------------------------------------------
    def select_end(self, which: Optional[str]) -> None:
        """Select the ``"start"`` or ``"end"`` of the edited line (``None`` clears).
        The section that :meth:`trim_end` would delete - the last segment and the
        curve attached to it, if any - is highlighted."""
        self._end = which if which in ("start", "end") else None
        self._overlay.update()

    def selected_end(self) -> Optional[str]:
        """The selected end, else the end whose handle is under the cursor."""
        if self._end:
            return self._end
        h, r = self._hot, self._route
        if r is not None and h is not None and h.kind == "anchor":
            if h.index == 0:
                return "start"
            if h.index == len(r.segs):
                return "end"
        return None

    def end_selection_points(self) -> List[g.Pt]:
        which = self.selected_end()
        if which is None or self._route is None or self._drag:
            return []
        return edit.span_points(self._route, edit.end_span(self._route, which))

    def trim_end(self, which: Optional[str] = None) -> bool:
        """Delete the last section from an end of the line - the selected end by
        default.  The end stays selected, so repeating it eats the line back
        section by section.  If nothing would be left, ``removalRequested`` is
        emitted instead.  Returns True if it acted."""
        which = which or self.selected_end()
        if which is None or self._route is None:
            return False
        new = edit.trim_end(self._route, which)
        if new is None:
            item = self._item
            self.stop()
            self.removalRequested.emit(item)
            return True
        self._end = which
        self.set_route(new)
        return True

    def delete_hot_anchor(self) -> bool:
        """Delete the anchor under the cursor.  Returns True if one was removed -
        lets an application's Delete shortcut try this before deleting the item."""
        h = self._hot
        if self._route is None or h is None or h.kind != "anchor":
            return False
        if not 0 < h.index < len(self._route.segs):
            return False
        self.set_route(edit.normalize(edit.delete_anchor(self._route, h.index)))
        self._hot = None
        return True

    # -------------------------------------------------------------- internals
    def _on_selection(self) -> None:
        if not (self._active and self.follow_selection) or self._drag:
            return
        try:
            sel = [it for it in self.scene.selectedItems() if self.route_of(it) is not None]
        except RuntimeError:                                  # scene is being torn down
            return
        if len(sel) == 1:
            if sel[0] is not self._item:
                self.edit(sel[0])
        elif self._item is not None:
            self.stop()

    def _scale(self) -> float:
        views = self.scene.views()
        return (abs(views[0].transform().m11()) or 1.0) if views else 1.0

    def _refresh(self) -> None:
        self._handles = edit.handles(self._route, self.show_section_grips) if self._route else []
        if self._route:
            l, t, r, b = self._route.bbox()
            m = (self.hit_px + 4.0) / self._scale()
            self._overlay.set_rect(QtCore.QRectF(l - m, t - m, (r - l) + 2 * m, (b - t) + 2 * m))
        self._overlay.setVisible(bool(self._route))
        self._overlay.update()

    def _push(self, route: Route) -> None:
        item = self._item
        if self.apply is not None:
            self.apply(item, route)
        else:
            path = route_to_path(route, self.corner_radius)
            item.setPath(item.mapFromScene(path))
            item._smartline_route = route
        self._refresh()
        self.routeChanged.emit(item, route)

    def _hit(self, p: g.Pt) -> Optional[edit.Handle]:
        tol = self.hit_px / self._scale()
        best, best_d = None, tol
        for h in self._handles:                               # controls are listed first
            d = g.dist(h.pos, p)
            if d < best_d - 1e-9:
                best, best_d = h, d
        return best

    def _snapped(self, qp, handle: Optional[edit.Handle]) -> g.Pt:
        is_end = (handle is not None and handle.kind == "anchor" and self._route is not None
                  and handle.index in (0, len(self._route.segs)))
        if is_end and self.snap_provider is not None:
            hit = self.snap_provider(qp)
            if hit is not None:
                return (hit.x(), hit.y())
        return g.snap_to_grid((qp.x(), qp.y()), self.grid)

    def _dragged(self, p: g.Pt, mods) -> Route:
        h, base, grab = self._drag
        ortho = self.keep_orthogonal and not _has(mods, _SHIFT)
        if h.kind == "anchor":
            return edit.move_anchor(base, h.index, p, keep_orthogonal=ortho)
        if h.kind == "segment":
            return edit.move_segment(base, h.index, p, grab, keep_orthogonal=ortho)
        link = "free" if _has(mods, _ALT) else "mirrored" if _has(mods, _CTRL) else self.link
        return edit.move_control(base, h.index, h.kind, p, link)

    # ----------------------------------------------------------- event filter
    def eventFilter(self, obj, ev):                           # noqa: N802
        if not self._active or obj is not self.scene or self._route is None:
            return False
        t = ev.type()
        if t == _EV_MOVE:
            sp = ev.scenePos()
            self._cursor = (sp.x(), sp.y())
            if self._drag:
                self._route = self._dragged(self._snapped(sp, self._drag[0]), ev.modifiers())
                self._push(self._route)
                return True
            hot = self._hit(self._cursor)
            if (hot is None) != (self._hot is None) or (hot and (hot.kind, hot.index) != (self._hot.kind, self._hot.index)):
                self._hot = hot
                self._overlay.update()
            return False
        if t == _EV_PRESS and ev.button() == _LEFT:
            sp = ev.scenePos()
            h = self._hit((sp.x(), sp.y()))
            if h is None:
                self._hot = None
                if self._end:
                    self.select_end(None)
                return False                                  # not ours: selection, moving ... carry on
            self._hot = h
            self._drag = (h, self._route, (sp.x(), sp.y()))
            return True
        if t == _EV_RELEASE and ev.button() == _LEFT and self._drag:
            base = self._drag[1]
            self._drag = None
            final = edit.normalize(self._route)
            changed = final.to_svg(4) != base.to_svg(4)
            self._route = final
            self._push(final)
            if changed:
                self.routeEdited.emit(self._item, base, final)
            else:                                             # a plain click on a line end selects it
                h = self._hot
                ends = {0: "start", len(final.segs): "end"}
                self.select_end(ends.get(h.index) if h is not None and h.kind == "anchor" else None)
            return True
        if t == _EV_DBL and ev.button() == _LEFT:
            sp = ev.scenePos()
            p = (sp.x(), sp.y())
            self._drag = None
            h = self._hit(p)
            if h is not None and h.kind == "anchor":
                self._hot = h
                self.delete_hot_anchor()
                return True
            s, tt, d = edit.nearest_segment(self._route, p)
            if d <= self.hit_px / self._scale():
                self.set_route(edit.insert_anchor(self._route, s, tt))
                return True
            return False
        if t == _EV_KEY:
            return self._key(_iv(ev.key()))
        return False

    def _key(self, k: int) -> bool:
        if k == _K("Key_Escape") and self._drag:
            self._route, self._drag = self._drag[1], None
            self._push(self._route)
            return True
        if k in (_K("Key_Delete"), _K("Key_Backspace")):
            return self.delete_hot_anchor() or self.trim_end()
        if k in (_K("Key_C"), _K("Key_L")):
            s, _, d = edit.nearest_segment(self._route, self._cursor)
            if d <= self.hit_px * 1.5 / self._scale():
                self.set_route(edit.convert_segment(self._route, s, "C" if k == _K("Key_C") else "L"))
                return True
        return False
