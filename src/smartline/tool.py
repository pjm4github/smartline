"""SmartLineTool - the interactive layer.  Attach it to any QGraphicsScene.

    tool = SmartLineTool(scene, modes=["ortho", "hug", "bus", "cubic"])
    tool.routeFinished.connect(lambda route: ...)  # smartline.Route
    tool.set_active(True)

Customising, from lightest to heaviest:
  attributes   clearance, grid, corner_radius, bus_pitch, pens, keymap ...
  hooks        snap_provider, obstacle_provider, obstacle_filter,
               guide_provider, item_factory
  subclassing  override make_item(), collect_obstacles(), collect_guides()
  routers      smartline.register(...) new modes or replace built-ins

It works as an *event filter* on the scene, so it needs no subclassing and can
be dropped into an existing application (your scene's own mouse handlers simply
don't see the clicks while the tool is active).

Default bindings while active (see ``DEFAULT_KEYMAP``; edit ``tool.keymap``)
    left click          set anchor / commit the previewed leg
    double click, Enter commit and finish
    right click         finish with what is committed (cancel if nothing is)
    Esc                 cancel the stroke
    Backspace           undo the last committed leg
    Space  or  /        toggle posture (HV <-> VH, diagonal-first <-> last ...)
    Shift+Space  or  M  next mode;   1..9 pick a mode directly
    Shift (held)        constrain straight legs to 45 degree steps
    G                   bus mode: cycle the guide net (nearest first, then auto)

Bus mode follows an existing net.  By default the guide is the wire nearest to
the anchor (within ``bus_capture``); it is highlighted while you draw.  Wires
are: everything this tool has drawn, plus QGraphicsLineItems and unfilled
QGraphicsPathItems in the scene - or whatever ``guide_provider`` returns.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Sequence, Union

from . import geometry as g
from .qt_compat import Qt, QtCore, QtGui, QtWidgets, Signal, enum
from . import registry, reroute as _reroute, tidy as _tidy
from .registry import default_routers
from .route import Route, concat
from .routers import RouteContext, Router

QPointF = QtCore.QPointF
QEvent = QtCore.QEvent

_T = lambda n: enum(QEvent, "Type", n)                       # noqa: E731
_EV_PRESS = _T("GraphicsSceneMousePress")
_EV_MOVE = _T("GraphicsSceneMouseMove")
_EV_DBL = _T("GraphicsSceneMouseDoubleClick")
_EV_KEY = _T("KeyPress")


def _iv(x) -> int:
    """Enum member or int -> int (PyQt6 enums are not always int-comparable)."""
    return int(getattr(x, "value", x))


_K = lambda n: _iv(enum(Qt, "Key", n))                       # noqa: E731
_LEFT = enum(Qt, "MouseButton", "LeftButton")
_RIGHT = enum(Qt, "MouseButton", "RightButton")
_NO_BUTTON = enum(Qt, "MouseButton", "NoButton")
_SHIFT = enum(Qt, "KeyboardModifier", "ShiftModifier")
_NO_BRUSH = enum(Qt, "BrushStyle", "NoBrush")


def _has(mods, flag) -> bool:
    try:
        return bool(mods & flag)
    except TypeError:                       # some enum flavours need .value
        return bool(_iv(mods) & _iv(flag))


# ------------------------------------------------------------------ painting

def polyline_path(points: Sequence) -> "QtGui.QPainterPath":
    path = QtGui.QPainterPath()
    if points:
        path.moveTo(points[0])
        for p in points[1:]:
            path.lineTo(p)
    return path


def rounded_path(points: Sequence, radius: float = 0.0) -> "QtGui.QPainterPath":
    """Polyline with filleted corners (quadratic blend, like a bend radius)."""
    pts = [(p.x(), p.y()) for p in points]
    if radius <= 0 or len(pts) < 3:
        return polyline_path([QPointF(*p) for p in pts])
    path = QtGui.QPainterPath()
    path.moveTo(*pts[0])
    for i in range(1, len(pts) - 1):
        a, v, b = pts[i - 1], pts[i], pts[i + 1]
        la, lb = g.dist(a, v), g.dist(v, b)
        r = min(radius, la / 2.0, lb / 2.0)
        if r < 1e-6:
            path.lineTo(*v)
            continue
        p1 = (v[0] + (a[0] - v[0]) * r / la, v[1] + (a[1] - v[1]) * r / la)
        p2 = (v[0] + (b[0] - v[0]) * r / lb, v[1] + (b[1] - v[1]) * r / lb)
        path.lineTo(*p1)
        path.quadTo(v[0], v[1], p2[0], p2[1])
    path.lineTo(*pts[-1])
    return path


def route_to_path(route: Route, corner_radius: float = 0.0) -> "QtGui.QPainterPath":
    """QPainterPath for a Route.  ``corner_radius`` fillets pure polylines."""
    if route.is_polyline:
        return rounded_path([QPointF(*p) for p in route.flatten()], corner_radius)
    path = QtGui.QPainterPath()
    path.moveTo(*route.start)
    for s in route.segs:
        flat = [c for p in s.pts for c in p]
        {"L": path.lineTo, "Q": path.quadTo, "C": path.cubicTo}[s.cmd](*flat)
    return path


#: key chord -> action.  Chords: optional "Shift+" then Space, Esc, Backspace,
#: Return, Enter, "/", a letter or a digit.  Actions: flip, next_mode,
#: prev_mode, cycle_guide, cancel, undo, finish, or "mode:<name|index>".
DEFAULT_KEYMAP: Dict[str, str] = {
    "Space": "flip", "/": "flip",
    "Shift+Space": "next_mode", "M": "next_mode", "Shift+M": "prev_mode",
    "G": "cycle_guide",
    "Esc": "cancel", "Backspace": "undo", "Return": "finish", "Enter": "finish",
    **{str(i + 1): f"mode:{i}" for i in range(9)},
}
_KEY_NAMES = {"Space": "Key_Space", "/": "Key_Slash", "Esc": "Key_Escape",
              "Backspace": "Key_Backspace", "Return": "Key_Return", "Enter": "Key_Enter"}
_NEEDS_STROKE = {"cancel", "undo", "finish"}


def _chord(text: str):
    shift = text.startswith("Shift+")
    tok = text[6:] if shift else text
    name = _KEY_NAMES.get(tok) or ("Key_" + tok.upper())
    return _K(name), shift


# ---------------------------------------------------------------------- tool

class SmartLineTool(QtCore.QObject):
    """Rubber-band line tool with pluggable routing modes."""

    routeFinished = Signal(object)     # smartline.Route - the whole connector
    lineFinished = Signal(object)      # list[QPointF] - same, flattened
    legCommitted = Signal(object)      # smartline.Route - the leg just placed
    previewChanged = Signal(object)    # smartline.Route or None - the live leg, on every update
    modeChanged = Signal(str)          # router.name
    postureChanged = Signal(bool)
    cancelled = Signal()
    routesRerouted = Signal(object)    # list[(item, old Route, new Route)] - one undoable batch

    def __init__(self, scene, modes: Optional[Iterable[Union[str, Router]]] = None,
                 parent=None):
        """*modes*: registry names and/or Router instances, in ring order
        (default: ``smartline.DEFAULT_MODES``)."""
        super().__init__(parent if parent is not None else scene)
        self.scene = scene
        self.routers: List[Router] = default_routers(modes)
        self.keymap: Dict[str, str] = dict(DEFAULT_KEYMAP)
        self._mode = 0
        self._active = False

        # --- behaviour knobs (plain attributes; change them any time) -------
        self.clearance = 8.0            # gap kept between a line and a shape
        self.bend_penalty = 40.0        # AvoidRouter: px of detour one bend is worth
        self.grid = 0.0                 # >0 snaps the cursor to a grid
        self.corner_radius = 0.0        # fillet radius of the finished path
        self.shift_angle_step = 45.0    # Shift constrains straight legs
        self.latch_distance = 6.0       # px of drag before posture is latched
        self.auto_add = True            # add a QGraphicsPathItem on finish
        self.pen = QtGui.QPen(QtGui.QColor(30, 30, 30), 2.0)
        self.preview_pen = QtGui.QPen(QtGui.QColor(40, 130, 220), 1.5,
                                      enum(Qt, "PenStyle", "DashLine"))
        self.preview_pen.setCosmetic(True)
        self.wire_spacing = 10.0        # re-routing: distance kept between parallel lines (0 = off)
        self.kink_length = 40.0         # a section shorter than this, between two bends, is a kink
        self.tidy_reroutes = True       # un-kink lines automatically after reroute_around()
        self.bus_pitch = 12.0           # spacing between bus members
        self.bus_capture = 60.0         # auto-pick radius for the guide net
        self.guide_pen = QtGui.QPen(QtGui.QColor(255, 170, 0, 140), 6.0)
        self.guide_pen.setCosmetic(True)
        #: ``f() -> iterable[list[QPointF]]`` - nets that bus mode may follow
        self.guide_provider: Optional[Callable] = None
        #: ``f(QPointF) -> QPointF | None`` - magnetic targets (ports, pins ...)
        self.snap_provider: Optional[Callable] = None
        #: ``f() -> iterable[QRectF]`` - replaces the default obstacle scan
        self.obstacle_provider: Optional[Callable] = None
        #: ``f(QGraphicsItem) -> bool`` - refine the default obstacle scan
        self.obstacle_filter: Optional[Callable] = None
        #: ``f(item) -> Route | None`` - where a connector item keeps its route
        self.route_of: Callable = lambda item: getattr(item, "_smartline_route", None)
        #: ``f(item, Route)`` - push a changed route back into a connector item
        self.apply_route: Optional[Callable] = None
        #: ``f(Route) -> QGraphicsItem`` - build your own connector item
        self.item_factory: Optional[Callable] = None

        # --- stroke state --------------------------------------------------
        self._legs: List[Route] = []        # committed legs
        self._anchor: Optional[g.Pt] = None
        self._cursor: Optional[g.Pt] = None
        self._live: List[g.Pt] = []         # flattened preview of the live leg
        self._live_route: Optional[Route] = None
        self._flip = False
        self._latch: Optional[str] = None
        self._shift = False
        self._obstacles: List[g.Rect] = []
        self._own_items: List = []
        self._wire_items: List = []          # connectors this tool created
        self._guides: List[List[g.Pt]] = []
        self._guide_pick: Optional[int] = None   # None = automatic (nearest)
        self._port_snapped = False

        self._committed_item = self._make_preview(solid=True)
        self._live_item = self._make_preview(solid=False)
        self._guide_item = self._make_preview(solid=True)
        self._guide_item.setPen(self.guide_pen)
        self._guide_item.setZValue(1e9 - 1)

    # ------------------------------------------------------------ public API
    @property
    def router(self) -> Router:
        return self.routers[self._mode]

    @property
    def mode(self) -> str:
        return self.router.name

    def set_mode(self, name_or_index) -> None:
        if isinstance(name_or_index, int):
            idx = name_or_index % len(self.routers)
        else:
            idx = [r.name for r in self.routers].index(name_or_index)
        if idx != self._mode:
            self._mode = idx
            self._flip = False
            self._reroute()
            self.modeChanged.emit(self.router.name)

    def next_mode(self) -> None:
        self.set_mode(self._mode + 1)

    def prev_mode(self) -> None:
        self.set_mode(self._mode - 1)

    def toggle_posture(self) -> None:
        self._flip = not self._flip
        self._reroute()
        self.postureChanged.emit(self._flip)

    def is_active(self) -> bool:
        return self._active

    def is_drawing(self) -> bool:
        return self._anchor is not None

    def set_active(self, on: bool) -> None:
        if on == self._active:
            return
        self._active = on
        if on:
            self.scene.installEventFilter(self)
            for v in self.scene.views():          # moves without a button held
                v.viewport().setMouseTracking(True)
        else:
            self.cancel()
            self.scene.removeEventFilter(self)

    def cancel(self) -> None:
        was = self.is_drawing()
        self._reset()
        if was:
            self.cancelled.emit()

    def undo_leg(self) -> None:
        if self._legs:
            leg = self._legs.pop()
            self._anchor = leg.start
            self._latch, self._flip = None, False
            self._reroute()
        elif self.is_drawing():
            self.cancel()

    def finish(self, commit_live: bool = True) -> None:
        if not self.is_drawing():
            return
        if commit_live:
            self._commit()
        route = self.route()
        self._reset()
        if route is None:
            self.cancelled.emit()
            return
        if self.auto_add:
            item = self.make_item(route)
            if item is not None:
                if item.scene() is None:
                    self.scene.addItem(item)
                item._smartline_route = route          # lets bus mode follow it
                self._wire_items.append(item)
        self.routeFinished.emit(route)
        self.lineFinished.emit([QPointF(x, y) for x, y in route.flatten()])

    def route(self) -> Optional[Route]:
        """Committed connector so far, or None if nothing is committed."""
        return concat(self._legs) if self._legs else None

    def points(self) -> List[g.Pt]:
        """Committed connector so far, flattened (scene coordinates)."""
        r = self.route()
        return r.flatten() if r else []

    # ------------------------------------------------- subclass override points
    def make_item(self, route: Route):
        """Build the finished connector item.  Override for custom item classes."""
        if self.item_factory is not None:
            return self.item_factory(route)
        item = QtWidgets.QGraphicsPathItem(route_to_path(route, self.corner_radius))
        item.setPen(self.pen)
        return item

    def collect_obstacles(self) -> List:
        """QRectFs (scene coordinates) that lines should keep clear of."""
        if self.obstacle_provider:
            return list(self.obstacle_provider())
        return [it.sceneBoundingRect() for it in self.scene.items() if self._is_obstacle(it)]

    def collect_guides(self) -> List[List[g.Pt]]:
        """Existing nets, as scene-space polylines, that bus mode may follow."""
        if self.guide_provider:
            return [[(p.x(), p.y()) for p in net] for net in self.guide_provider()]
        nets: List[List[g.Pt]] = []
        self._wire_items = [it for it in self._wire_items if it.scene() is self.scene]
        for it in self.scene.items():
            if it in self._own_items or not it.isVisible():
                continue
            r = self.route_of(it)
            pts = r.flatten() if r is not None else self._item_polyline(it)
            if pts:
                nets.append(list(pts))
        return nets

    # ------------------------------------------------------------ re-routing
    def wires(self) -> List:
        """``[(item, Route), ...]`` for every connector in the scene that carries a route."""
        out = []
        for it in self.scene.items():
            if it in self._own_items:
                continue
            r = self.route_of(it)
            if r is not None:
                out.append((it, r))
        return out

    def reroute_around(self, shape, wires: Optional[Sequence] = None) -> List:
        """Repair every line that *shape* now overlaps.

        *shape* is a QGraphicsItem (its ``sceneBoundingRect()`` is used) or a
        QRectF in scene coordinates.  Only the part of each line that runs
        through the shape is replaced, using the routing method that part was
        drawn with (``ortho`` stays orthogonal, ``cubic`` stays a curve, ...),
        and going around the shape at the tool's current ``clearance``.

        Returns - and emits as ``routesRerouted`` - a list of
        ``(item, old_route, new_route)``, ready for an undo command.  A new route
        whose ``meta["unresolved"]`` is set could not be cleared (typically a
        line end lies inside the shape).

        Lines are repaired one after another and each sees the ones already
        repaired: a new piece may cross another line but never runs along it -
        it takes the next lane out, ``wire_spacing`` apart (``meta["overlaps"]``
        is set if no free lane was found).
        """
        r = shape.sceneBoundingRect() if hasattr(shape, "sceneBoundingRect") else shape
        rect = (r.left(), r.top(), r.right(), r.bottom())
        self.refresh_obstacles()
        obstacles = list(self._obstacles)
        if rect not in obstacles:                 # a shape the default scan filters out still counts
            obstacles.append(rect)
        pairs = list(wires) if wires is not None else self.wires()
        latest = {id(item): route for item, route in pairs}       # repaired lines count at once
        changes = []
        for item, route in pairs:
            if not _reroute.hits(route, rect):
                continue
            others = [latest[id(it)].flatten() for it, _r in pairs if it is not item]
            ctx = RouteContext(obstacles=obstacles, clearance=self.clearance,
                               bend_penalty=self.bend_penalty,
                               guides=[g.simplify(o) for o in others],
                               bus_pitch=self.bus_pitch, bus_capture=self.bus_capture,
                               wire_spacing=self.wire_spacing)
            new = _reroute.repair(route, rect, ctx, lookup=self.router_named, others=others)
            if new is not None and self.tidy_reroutes:
                new = _tidy.unkink(new, ctx, self.kink_length, others) or new
            if new is not None and new.to_svg(4) != route.to_svg(4):
                self.set_item_route(item, new)
                latest[id(item)] = new
                changes.append((item, route, new))
        if changes:
            self.routesRerouted.emit(changes)
        return changes

    def _scene_context(self, item):
        """``(RouteContext, other lines' polylines)`` as seen by connector *item*."""
        self.refresh_obstacles()
        others = [r.flatten() for it, r in self.wires() if it is not item]
        ctx = RouteContext(obstacles=list(self._obstacles), clearance=self.clearance,
                           bend_penalty=self.bend_penalty, guides=[g.simplify(o) for o in others],
                           bus_pitch=self.bus_pitch, bus_capture=self.bus_capture,
                           wire_spacing=self.wire_spacing)
        return ctx, others

    def _commit_change(self, item, old: Route, new: Optional[Route]) -> List:
        if new is None or new.to_svg(4) == old.to_svg(4):
            return []
        self.set_item_route(item, new)
        changes = [(item, old, new)]
        self.routesRerouted.emit(changes)
        return changes

    def unkink(self, item, max_jog: Optional[float] = None) -> List:
        """Remove kinks from connector *item*: wherever the line changes direction
        three times within ``kink_length``, the short section in the middle is
        collapsed, which removes its two bends (spurs and hairpins vanish).  Line
        ends never move, shapes keep their clearance and the line is never slid
        onto another one.  Returns / emits ``[(item, old, new)]`` (empty if clean).
        """
        route = self.route_of(item)
        if route is None:
            return []
        ctx, others = self._scene_context(item)
        return self._commit_change(item, route,
                                   _tidy.unkink(route, ctx, max_jog or self.kink_length, others))

    def follow_bus(self, item, guide=None) -> List:
        """Re-route connector *item* as a bus member of another line.

        *guide* is the connector to follow; by default the line *item* already
        runs closest to over its whole length (not just at one end).  The line
        keeps its own end points, runs parallel to the guide at ``bus_pitch`` on
        the side it already favours - in the first lane no other line occupies -
        goes around shapes, and is un-kinked.  Returns / emits ``[(item, old, new)]``.
        """
        route = self.route_of(item)
        if route is None:
            return []
        ctx, others = self._scene_context(item)
        pairs = [(it, r) for it, r in self.wires() if it is not item]
        if guide is not None:
            guide_route = self.route_of(guide)
        else:
            idx = _tidy.nearest_route(route, [r.flatten() for _it, r in pairs])
            guide_route = pairs[idx][1] if idx is not None else None
        if guide_route is None:
            return []
        new = _tidy.follow(route, guide_route.flatten(), ctx, others, max_jog=self.kink_length)
        return self._commit_change(item, route, new)

    def trim_end(self, item, which: str = "end") -> List:
        """Delete the last section from the ``"start"`` or ``"end"`` of connector
        *item*, together with the curve that attaches it to the rest of the line.
        Returns / emits ``[(item, old, new)]``; ``new`` is ``None`` when nothing
        would be left, in which case the item is untouched and the caller should
        delete it."""
        route = self.route_of(item)
        if route is None:
            return []
        from . import edit as _edit
        new = _edit.trim_end(route, which)
        if new is None:
            return [(item, route, None)]
        return self._commit_change(item, route, new)

    def reroute_colliding(self) -> List:
        """``reroute_around`` for every obstacle in the scene - a global clean-up."""
        changes = []
        for qrect in self.collect_obstacles():
            changes.extend(self.reroute_around(qrect))
        return changes

    def router_named(self, name: Optional[str]) -> Optional[Router]:
        """This tool's router for a mode name, else the registry's, else None."""
        for r in self.routers:
            if r.name == name:
                return r
        try:
            return registry.create(name) if name else None
        except KeyError:
            return None

    def set_item_route(self, item, route: Route) -> None:
        """Write *route* into a connector item (honours ``apply_route``)."""
        if self.apply_route is not None:
            self.apply_route(item, route)
            return
        item.setPath(item.mapFromScene(route_to_path(route, self.corner_radius)))
        item._smartline_route = route

    def refresh_obstacles(self) -> None:
        self._obstacles = [(r.left(), r.top(), r.right(), r.bottom())
                           for r in self.collect_obstacles() if r.width() > 0 and r.height() > 0]

    def refresh_guides(self) -> None:
        self._guides = [n for n in (g.simplify(n) for n in self.collect_guides()) if len(n) >= 2]

    @staticmethod
    def _item_polyline(item) -> Optional[List[g.Pt]]:
        """Scene-space polyline of a foreign wire-like item, else None."""
        if isinstance(item, QtWidgets.QGraphicsLineItem):
            ln = item.line()
            qs = [item.mapToScene(ln.p1()), item.mapToScene(ln.p2())]
        elif isinstance(item, QtWidgets.QGraphicsPathItem) and item.brush().style() == _NO_BRUSH:
            polys = item.path().toSubpathPolygons()
            if not polys:
                return None
            poly = polys[0]
            qs = [item.mapToScene(poly.at(i)) for i in range(poly.count())]
        else:
            return None
        return [(q.x(), q.y()) for q in qs]

    def cycle_guide(self) -> None:
        """G key: step through the nets, nearest to the anchor first; after the
        last one return to automatic picking."""
        if not self._guides:
            return
        nxt = 0 if self._guide_pick is None else self._guide_pick + 1
        self._guide_pick = nxt if nxt < len(self._guides) else None
        self._reroute()

    def _guide_order(self) -> List[List[g.Pt]]:
        ref = self._anchor or self._cursor or (0.0, 0.0)
        return sorted(self._guides, key=lambda n: g.project_on_polyline(n, ref)[0])

    # -------------------------------------------------------------- internals
    def _make_preview(self, solid: bool):
        item = QtWidgets.QGraphicsPathItem()
        pen = QtGui.QPen(self.preview_pen)
        if solid:
            pen.setStyle(enum(Qt, "PenStyle", "SolidLine"))
        item.setPen(pen)
        item.setZValue(1e9)
        item.setAcceptedMouseButtons(_NO_BUTTON)
        item.setVisible(False)
        self.scene.addItem(item)
        self._own_items.append(item)
        return item

    def _is_obstacle(self, item) -> bool:
        if item in self._own_items or not item.isVisible() or item.parentItem() is not None:
            return False
        if self.obstacle_filter is not None:
            return bool(self.obstacle_filter(item))
        if isinstance(item, QtWidgets.QGraphicsLineItem):
            return False                                  # other wires
        if isinstance(item, QtWidgets.QGraphicsPathItem) and item.brush().style() == _NO_BRUSH:
            return False                                  # unfilled paths = wires
        return True

    def _context(self) -> RouteContext:
        prev = None
        if self._legs:
            tail = self._legs[-1].flatten()
            prev = g.heading(tail[-2], tail[-1]) if len(tail) >= 2 else None
        return RouteContext(obstacles=self._obstacles, clearance=self.clearance,
                            flip=self._flip, prev_heading=prev,
                            auto_posture=self._latch,
                            angle_step=self.shift_angle_step if self._shift else 0.0,
                            bend_penalty=self.bend_penalty,
                            guides=self._guides,
                            guide=(self._guide_order()[self._guide_pick]
                                   if self._guide_pick is not None and self._guides else None),
                            bus_pitch=self.bus_pitch, bus_capture=self.bus_capture)

    def _snap(self, qp) -> g.Pt:
        self._port_snapped = False
        if self.snap_provider is not None:
            hit = self.snap_provider(qp)
            if hit is not None:
                self._port_snapped = True
                return (hit.x(), hit.y())
        return g.snap_to_grid((qp.x(), qp.y()), self.grid)

    def _reroute(self) -> None:
        if self._anchor is None or self._cursor is None:
            self._live, self._live_route = [], None
        else:
            a, c = self._anchor, self._cursor
            dx, dy = abs(c[0] - a[0]), abs(c[1] - a[1])
            if max(dx, dy) < self.latch_distance:
                self._latch = None             # back at the anchor: re-decide
            elif self._latch is None:
                self._latch = "HV" if dx >= dy else "VH"
            self._live_route = Route.coerce(self.router.shape(a, c, self._context()))
            self._live_route.meta.update(router=self.router.name, flip=self._flip,
                                         posture=self._latch)
            self._live = self._live_route.flatten()
        self._repaint()

    def _repaint(self) -> None:
        drawing = self.is_drawing()
        self._committed_item.setVisible(drawing)
        self._live_item.setVisible(drawing)
        guide = None
        if drawing and hasattr(self.router, "pick_guide"):
            guide = self.router.pick_guide(self._anchor, self._context())
        self._guide_item.setVisible(guide is not None)
        if guide is not None:
            self._guide_item.setPath(polyline_path([QPointF(*p) for p in guide]))
        if drawing:
            done = self.route()
            self._committed_item.setPath(route_to_path(done) if done else QtGui.QPainterPath())
            self._live_item.setPath(route_to_path(self._live_route) if self._live_route
                                    else QtGui.QPainterPath())
        self.previewChanged.emit(self._live_route if drawing else None)

    def _commit(self) -> None:
        leg = self._live_route
        if leg is not None and leg.segs and g.dist(leg.start, leg.end) > 1e-6:
            self._legs.append(leg)
            self._anchor = leg.end
            self._latch, self._flip = None, False
            self.refresh_obstacles()
            self.legCommitted.emit(leg)
            self._reroute()

    def _reset(self) -> None:
        self._legs, self._live, self._live_route = [], [], None
        self._anchor = self._cursor = None
        self._latch, self._flip = None, False
        self._guide_pick = None
        self._repaint()

    # ---------------------------------------------------------- event filter
    def eventFilter(self, obj, ev):          # noqa: N802 (Qt naming)
        if not self._active or obj is not self.scene:
            return False
        t = ev.type()
        if t == _EV_MOVE:
            self._shift = _has(ev.modifiers(), _SHIFT)
            self._cursor = self._snap(ev.scenePos())
            if self.is_drawing():
                self._reroute()
            return False                      # let hover effects keep working
        if t == _EV_PRESS:
            if ev.button() == _LEFT:
                self._cursor = self._snap(ev.scenePos())
                if not self.is_drawing():
                    self._anchor = self._cursor
                    self.refresh_obstacles()
                    self.refresh_guides()
                    if not self._port_snapped:     # e.g. pull onto a bus lane
                        self._anchor = self.router.constrain_start(self._anchor, self._context())
                    self._reroute()
                else:
                    self._reroute()
                    self._commit()
                return True
            if ev.button() == _RIGHT and self.is_drawing():
                self.finish(commit_live=False)
                return True
            return False
        if t == _EV_DBL and ev.button() == _LEFT:
            if self.is_drawing():             # the 1st click already committed
                self.finish(commit_live=True)
            return True
        if t == _EV_KEY:
            return self._key(ev)
        return False

    def _key(self, ev) -> bool:
        k, shift = _iv(ev.key()), _has(ev.modifiers(), _SHIFT)
        action = None
        for exact in (True, False):                 # "Shift+X" beats plain "X"
            for chord, act in self.keymap.items():
                ck, cshift = _chord(chord)
                if ck == k and ((cshift == shift) if exact else not cshift):
                    action = act
                    break
            if action:
                break
        if action is None or (action in _NEEDS_STROKE and not self.is_drawing()):
            return False
        return self.do(action)

    def do(self, action: str) -> bool:
        """Run a keymap action by name (also handy for toolbar buttons)."""
        if action.startswith("mode:"):
            arg = action[5:]
            idx = int(arg) if arg.isdigit() else [r.name for r in self.routers].index(arg)
            if idx < len(self.routers):
                self.set_mode(idx)
            return True
        fn = {"flip": self.toggle_posture, "next_mode": self.next_mode,
              "prev_mode": self.prev_mode, "cycle_guide": self.cycle_guide,
              "cancel": self.cancel, "undo": self.undo_leg, "finish": self.finish}.get(action)
        if fn is None:
            return False
        fn()
        return True
