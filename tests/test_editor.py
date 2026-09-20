"""RouteEditor state machine - real Qt (offscreen) when installed, else the stub."""
from tests.test_tool import (Ev, LEFT, NOMOD, REAL, T, enum, Qt, QtWidgets, key as _tool_key)  # noqa: F401
from smartline import Route
from smartline.editor import RouteEditor

SHIFT = enum(Qt, "KeyboardModifier", "ShiftModifier")
ALT = enum(Qt, "KeyboardModifier", "AltModifier")


def make(route):
    scene = QtWidgets.QGraphicsScene()
    item = QtWidgets.QGraphicsPathItem()
    item._smartline_route = route
    scene.addItem(item)
    scene._keep = [item]
    ed = RouteEditor(scene)
    ed.set_active(True)
    log = []
    ed.routeEdited.connect(lambda it, old, new: log.append((old, new)))
    assert ed.edit(item)
    return scene, item, ed, log


def send(ed, *a, **k):
    return ed.eventFilter(ed.scene, Ev(*a, **k))


def drag(ed, a, b, mods=NOMOD):
    assert send(ed, T("GraphicsSceneMousePress"), a) is True
    send(ed, T("GraphicsSceneMouseMove"), b, mods=mods)
    send(ed, T("GraphicsSceneMouseRelease"), b)


Z = Route.from_points([(0, 0), (100, 0), (100, 80), (200, 80)])
S = Route((0, 0)).cubic_to((50, 0), (50, 60), (100, 60)).cubic_to((150, 60), (150, 0), (200, 0))


def test_drag_section_then_undo_record():
    scene, item, ed, log = make(Z)
    drag(ed, (100, 40), (140, 45))                               # grip of the vertical section
    assert ed.route().anchors() == [(0, 0), (140, 0), (140, 80), (200, 80)]
    assert item._smartline_route is ed.route()
    assert len(log) == 1 and log[0][0] is Z                      # one undo step per gesture


def test_click_away_from_handles_is_not_swallowed():
    scene, item, ed, log = make(Z)
    assert send(ed, T("GraphicsSceneMousePress"), (400, 400)) is False and not log


def test_shift_frees_a_vertex_and_escape_abandons():
    scene, item, ed, log = make(Z)
    drag(ed, (100, 0), (120, 30), mods=SHIFT)
    assert ed.route().anchors()[1] == (120, 30)
    before = ed.route()
    send(ed, T("GraphicsSceneMousePress"), (200, 80))
    send(ed, T("GraphicsSceneMouseMove"), (260, 140))
    ed.eventFilter(ed.scene, Ev(T("KeyPress"), key=enum(Qt, "Key", "Key_Escape")))
    assert ed.route() is before


def test_tangent_handles_aligned_and_broken_with_alt():
    scene, item, ed, log = make(S)
    drag(ed, (50, 60), (100, 20))                                # c2 of the first curve
    assert ed.route().segs[1].pts[0] == (100, 110)               # partner stays collinear
    scene, item, ed, log = make(S)
    drag(ed, (50, 60), (100, 20), mods=ALT)
    assert ed.route().segs[1].pts[0] == (150, 60)                # cusp: partner untouched


def test_double_click_inserts_and_deletes_and_keys_convert():
    scene, item, ed, log = make(Z)
    assert send(ed, T("GraphicsSceneMouseDoubleClick"), (30, 0)) is True
    assert len(ed.route().segs) == 4
    assert send(ed, T("GraphicsSceneMouseDoubleClick"), (30, 0)) is True      # on the new anchor
    assert len(ed.route().segs) == 3
    send(ed, T("GraphicsSceneMouseMove"), (100, 30))
    ed.eventFilter(ed.scene, Ev(T("KeyPress"), key=enum(Qt, "Key", "Key_C")))
    assert [s.cmd for s in ed.route().segs] == ["L", "C", "L"]


def test_follows_selection():
    scene, item, ed, log = make(Z)
    ed.stop()
    assert not ed.is_editing()
    if REAL:
        item.setFlag(enum(QtWidgets.QGraphicsItem, "GraphicsItemFlag", "ItemIsSelectable"), True)
    item.setSelected(True)
    assert ed.is_editing() and ed.item() is item
    item.setSelected(False)
    assert not ed.is_editing()
    ed.set_active(False)
