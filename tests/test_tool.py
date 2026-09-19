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
    n_items = len(scene.items())
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
    assert len(scene.items()) == n_items + 1                     # path item added


def test_hug_mode_sees_scene_obstacles():
    scene, tool, done = make()
    tool.set_mode("hug")
    tool.clearance = 10
    send(tool, T("GraphicsSceneMousePress"), (0, 150))
    send(tool, T("GraphicsSceneMouseMove"), (300, 150))
    ys = {p[1] for p in tool._live}
    assert len(tool._live) > 2 and (90 in ys or 210 in ys), tool._live


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
