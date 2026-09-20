"""State-machine tests for SmartLineTool.

Uses the real Qt binding (offscreen) when one is installed, otherwise a tiny
stub - the tool's eventFilter is driven with duck-typed events either way.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from smartline import qt_compat                       # real Qt?
    REAL = True
except ImportError:
    from tests import _qt_stub
    _qt_stub.install()
    sys.modules.pop("smartline.qt_compat", None)
    from smartline import qt_compat
    REAL = False

from smartline.qt_compat import Qt, QtCore, QtWidgets, enum   # noqa: E402
from smartline.tool import SmartLineTool                       # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([]) if REAL else None
T = lambda n: enum(QtCore.QEvent, "Type", n)                   # noqa: E731
LEFT = enum(Qt, "MouseButton", "LeftButton")
RIGHT = enum(Qt, "MouseButton", "RightButton")
NOMOD = enum(Qt, "KeyboardModifier", "NoModifier")


class Ev:
    def __init__(self, type_, pos=(0, 0), button=LEFT, key=None, mods=NOMOD):
        self._t, self._p, self._b, self._k, self._m = type_, pos, button, key, mods
    def type(self): return self._t
    def scenePos(self): return QtCore.QPointF(*self._p)
    def button(self): return self._b
    def key(self): return self._k
    def modifiers(self): return self._m


def make():
    scene = QtWidgets.QGraphicsScene()
    box = QtWidgets.QGraphicsRectItem(100, 100, 100, 100)
    if REAL:
        from smartline.qt_compat import QtGui
        box.setBrush(QtGui.QBrush(QtGui.QColor(200, 200, 200)))
    scene.addItem(box)
    scene._keep = [box]            # keep Python refs alive for the whole test (binding-agnostic)
    tool = SmartLineTool(scene)
    tool.set_active(True)
    done = []
    tool.lineFinished.connect(done.append)
    return scene, tool, done


def send(tool, *a, **k):
    return tool.eventFilter(tool.scene, Ev(*a, **k))


def key(tool, name, mods=NOMOD):
    return send(tool, T("KeyPress"), key=enum(Qt, "Key", name), mods=mods)


def xy(pts):
    return [(p.x(), p.y()) for p in pts]


def test_ortho_stroke_with_flip_undo_finish():
    scene, tool, done = make()
    tool.set_mode("ortho")
    before = list(scene.items())
    assert send(tool, T("GraphicsSceneMousePress"), (0, 0)) is True
    send(tool, T("GraphicsSceneMouseMove"), (60, 10))          # latches HV
    send(tool, T("GraphicsSceneMouseMove"), (60, 80))
    assert tool._live == [(0, 0), (60, 0), (60, 80)]
    key(tool, "Key_Space")                                       # flip -> VH
    assert tool._live == [(0, 0), (0, 80), (60, 80)]
    send(tool, T("GraphicsSceneMousePress"), (60, 80))         # commit leg 1
    send(tool, T("GraphicsSceneMouseMove"), (90, 300))
    send(tool, T("GraphicsSceneMousePress"), (90, 300))        # commit leg 2
    assert len(tool._legs) == 2
    key(tool, "Key_Backspace")                                   # undo leg 2
    assert len(tool._legs) == 1 and tool._anchor == (60, 80)
    send(tool, T("GraphicsSceneMouseMove"), (60, 200))
    key(tool, "Key_Return")
    assert xy(done[0]) == [(0, 0), (0, 80), (60, 80), (60, 200)]
    assert not tool.is_drawing()
    added = [it for it in scene.items() if it not in before]
    assert len(added) == 1 and isinstance(added[0], QtWidgets.QGraphicsPathItem), (
        [type(i).__name__ for i in before], [type(i).__name__ for i in scene.items()])
    assert added[0] in tool._wire_items                           # bus mode can follow it


def test_hug_mode_sees_scene_obstacles():
    scene, tool, done = make()
    tool.set_mode("hug")
    tool.clearance = 10
    send(tool, T("GraphicsSceneMousePress"), (0, 150))
    send(tool, T("GraphicsSceneMouseMove"), (300, 150))
    # hull = sceneBoundingRect (includes half the shape's pen width) + clearance
    ys = [p[1] for p in tool._live]
    assert len(tool._live) > 2 and any(abs(y - 90) <= 1 or abs(y - 210) <= 1 for y in ys), tool._live


def test_modes_cycle_escape_and_right_click():
    scene, tool, done = make()
    names = [r.name for r in tool.routers]
    key(tool, "Key_M")
    assert tool.mode == names[1]
    key(tool, "Key_1")
    assert tool.mode == names[0]
    send(tool, T("GraphicsSceneMousePress"), (0, 0))
    key(tool, "Key_Escape")
    assert not tool.is_drawing() and not done
    send(tool, T("GraphicsSceneMousePress"), (0, 0))
    send(tool, T("GraphicsSceneMouseMove"), (50, 50))
    send(tool, T("GraphicsSceneMousePress"), (50, 50))
    send(tool, T("GraphicsSceneMouseMove"), (500, 500))
    send(tool, T("GraphicsSceneMousePress"), (500, 500), button=RIGHT)
    assert xy(done[0]) == [(0, 0), (50, 50)]
    tool.set_active(False)


def test_bus_mode_follows_previous_wire():
    scene, tool, done = make()
    tool.set_mode("ortho")
    send(tool, T("GraphicsSceneMousePress"), (300, 300))
    send(tool, T("GraphicsSceneMouseMove"), (600, 320))          # HV
    send(tool, T("GraphicsSceneMouseMove"), (600, 500))
    key(tool, "Key_Return")
    assert xy(done[0]) == [(300, 300), (600, 300), (600, 500)]
    tool.set_mode("bus")
    send(tool, T("GraphicsSceneMousePress"), (300, 313))         # 1 px off lane 1
    assert tool._anchor == (300, 312)                            # pulled onto it
    send(tool, T("GraphicsSceneMouseMove"), (588, 500))
    assert tool._live == [(300, 312), (588, 312), (588, 500)]
    key(tool, "Key_Space")                                       # other side
    assert (612, 288) in tool._live
    key(tool, "Key_G")                                           # explicit pick
    assert tool._guide_pick == 0
    key(tool, "Key_G")
    assert tool._guide_pick is None                              # back to auto
    key(tool, "Key_Escape")


def test_cubic_mode_emits_route_and_custom_item_and_keymap():
    scene, tool, done = make()
    routes, made = [], []
    tool.routeFinished.connect(routes.append)
    tool.item_factory = lambda route: made.append(route) or None   # app builds its own item
    tool.keymap["C"] = "mode:cubic"
    key(tool, "Key_C")
    assert tool.mode == "cubic"
    send(tool, T("GraphicsSceneMousePress"), (0, 0))
    send(tool, T("GraphicsSceneMouseMove"), (100, 60))
    key(tool, "Key_Return")
    assert routes[0].to_svg() == "M 0 0 C 50 0 50 60 100 60" and made == routes
    assert xy(done[0])[0] == (0, 0) and xy(done[0])[-1] == (100, 60) and len(done[0]) > 8


def test_modes_argument_selects_and_orders_the_ring():
    scene = QtWidgets.QGraphicsScene()
    tool = SmartLineTool(scene, modes=["bus", "ortho"])
    assert [r.name for r in tool.routers] == ["bus", "ortho"]


def test_reroute_around_a_moved_shape_keeps_each_lines_method():
    scene, tool, done = make()                                   # box at (100,100)-(200,200)
    batches = []
    tool.routesRerouted.connect(batches.append)
    tool.clearance = 10
    for mode, y in (("ortho", 300.0), ("cubic", 320.0)):         # both pass well below the box
        tool.set_mode(mode)
        send(tool, T("GraphicsSceneMousePress"), (0, y))
        send(tool, T("GraphicsSceneMouseMove"), (300, y))
        key(tool, "Key_Return")
    assert len(tool.wires()) == 2 and tool.reroute_around(scene._keep[0]) == []
    moved = QtCore.QRectF(100, 270, 100, 80)                     # "drop" a shape onto both lines
    changes = tool.reroute_around(moved)
    assert len(changes) == 2 and batches == [changes]
    by_mode = {old.meta["router"]: new for _item, old, new in changes}
    rect = (100, 270, 200, 350)
    from smartline import edit, reroute
    assert edit.is_orthogonal(by_mode["ortho"]) and not reroute.hits(by_mode["ortho"], rect)
    assert not by_mode["cubic"].is_polyline and not reroute.hits(by_mode["cubic"], rect)
    for item, _old, new in changes:
        assert tool.route_of(item) is new                        # written back into the items
    assert tool.reroute_around(moved) == []                      # idempotent


def test_unkink_and_follow_bus_on_selected_lines():
    from smartline import Route, geometry as geo
    scene, tool, done = make()                                   # one box at (100,100)-(200,200)
    guide = QtWidgets.QGraphicsPathItem()
    guide._smartline_route = Route.from_points([(0, 300), (300, 300), (300, 500), (600, 500)])
    messy = QtWidgets.QGraphicsPathItem()
    messy._smartline_route = Route.from_points([(0, 330), (150, 330), (150, 338), (260, 338), (260, 480),
                                                (263, 480), (263, 420), (280, 420), (280, 540), (600, 540)])
    for it in (guide, messy):
        scene.addItem(it)
    scene._keep += [guide, messy]
    batches = []
    tool.routesRerouted.connect(batches.append)

    (item, old, new), = tool.unkink(messy)
    assert item is messy and geo.bends(new.flatten()) < geo.bends(old.flatten())
    assert new.start == old.start and new.end == old.end and tool.unkink(messy) == []

    (item, old, new), = tool.follow_bus(messy)                   # guide found automatically
    a = new.anchors()
    assert (288, 312) in a and (288, 512) in a                   # parallel to the guide, one pitch out
    assert new.start == (0, 330) and new.end == (600, 540) and len(batches) == 2
    assert tool.follow_bus(messy, guide=guide) == []             # explicit guide; already following -> no-op


def test_reroute_with_refresh_picks_up_every_changed_setting():
    scene, tool, done = make()                                   # box at (100,100)-(200,200), 1 px pen
    applied = []
    real = tool.set_item_route
    tool.apply_route = lambda item, route: (applied.append(route), setattr(item, "_smartline_route", route))
    tool.clearance = 10
    tool.set_mode("hug")
    send(tool, T("GraphicsSceneMousePress"), (0, 150))
    send(tool, T("GraphicsSceneMouseMove"), (300, 150))
    key(tool, "Key_Return")
    (wire, route), = tool.wires()
    top = lambda r: min(p[1] for p in r.anchors())               # noqa: E731
    box = scene._keep[0]
    assert abs(top(route) - 90) <= 1

    tool.clearance = 30
    assert tool.reroute_around(box) == []                        # default: the line is clear, untouched
    (item, old, new), = tool.reroute_around(box, refresh=True)
    assert abs(top(new) - 70) <= 1 and tool.route_of(wire) is new

    tool.clearance = 5
    (item, old, new), = tool.refresh_routes([wire])
    assert abs(top(new) - 95) <= 1

    applied.clear()
    tool.corner_radius = 20                                      # geometry unchanged, drawing changed
    assert tool.reroute_around(box, refresh=True) == [] and len(applied) == 1


def test_drawing_from_a_port_leaves_squarely_for_the_clearance():
    scene, tool, done = make()                                   # box (100,100)-(200,200)
    tool.clearance = 12
    for mode in ("ortho", "hug", "avoid", "cubic", "straight"):
        tool.set_mode(mode)
        send(tool, T("GraphicsSceneMousePress"), (200, 150))     # on the right-hand side: a port
        send(tool, T("GraphicsSceneMouseMove"), (120, 320))      # target is behind and below the shape
        p0, p1 = tool._live[0], tool._live[1]
        assert p0 == (200, 150) and abs(p1[1] - 150) < 1e-6 and 211.5 <= p1[0] - 0 <= 213.5, (mode, tool._live[:3])
        key(tool, "Key_Escape")
    tool.port_exits = False
    tool.set_mode("straight")
    send(tool, T("GraphicsSceneMousePress"), (200, 150))
    send(tool, T("GraphicsSceneMouseMove"), (120, 320))
    assert tool._live == [(200, 150), (120, 320)]


def test_moving_a_shape_keeps_its_lines_connected_and_routed_by_the_rules():
    from smartline import edit, ports, reroute
    scene, tool, done = make()                                   # shape A: (100,100)-(200,200)
    a = scene._keep[0]
    b = QtWidgets.QGraphicsRectItem(400, 300, 100, 100)
    if REAL:
        from smartline.qt_compat import QtGui
        b.setBrush(QtGui.QBrush(QtGui.QColor(200, 200, 200)))
    scene.addItem(b)
    scene._keep.append(b)
    tool.clearance = 12
    tool.set_mode("hug")
    send(tool, T("GraphicsSceneMousePress"), (200, 150))         # A's right side ...
    send(tool, T("GraphicsSceneMouseMove"), (400, 350))          # ... to B's left side
    key(tool, "Key_Return")
    tool.set_mode("ortho")
    send(tool, T("GraphicsSceneMousePress"), (0, 450))           # a bystander line, not attached
    send(tool, T("GraphicsSceneMouseMove"), (300, 450))
    send(tool, T("GraphicsSceneMouseMove"), (300, 451))
    key(tool, "Key_Return")
    wires = {r.legs()[0]["router"]: it for it, r in tool.wires()}
    link = tool.links(wires["hug"])
    assert link["start"][0] is a and link["end"][0] is b and tool.links(wires["ortho"]) == {"start": None, "end": None}
    assert tool.linked_wires(a) == [(wires["hug"], "start")]
    before = tool.route_of(wires["hug"])

    batches = []
    tool.routesRerouted.connect(batches.append)
    a.setPos(-30, 150)                                           # live drag step: A now (70,250)-(170,350)
    assert tool.shape_moved(a, record=False) == [] and batches == []
    a.setPos(40, 290)                                            # dropped at (140,390)-(240,490): on the bystander
    changes = tool.shape_moved(a)
    assert len(batches) == 1 and {id(i) for i, _o, _n in changes} == {id(w) for w in wires.values()}
    old = {id(i): o for i, o, _n in changes}
    assert old[id(wires["hug"])] is before                      # undo goes back to before the drag

    moved = tool.route_of(wires["hug"])
    port = (240, 440)                                            # the same spot on A's right side
    assert g_dist(moved.start, port) < 1.0 and moved.end == before.end
    rects = [(140, 390, 240, 490), (400, 300, 500, 400)]
    find = ports.rect_exit_finder(rects, 3.0)
    assert ports.stubs_ok(moved.flatten(), 12, find(moved.start, 12), find(moved.end, 12)), moved.to_svg(0)
    assert ports.obstacle_hits(moved.flatten(), rects, find(moved.start, 12), find(moved.end, 12)) == 0
    assert edit.is_orthogonal(moved) and moved.legs()[0]["router"] == "hug"
    assert not reroute.hits(tool.route_of(wires["ortho"]), rects[0])     # the bystander was repaired too
    assert tool.links(wires["hug"])["start"][0] is a             # still connected for the next move


def g_dist(p, q):
    return ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5
