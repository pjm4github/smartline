"""Minimal fake PyQt6 so the tool's state machine can be tested on a machine
without Qt (CI containers).  Only what smartline.tool touches is modelled."""
import enum
import sys
import types


def install():
    core, gui, wid = (types.ModuleType("PyQt6." + n) for n in ("QtCore", "QtGui", "QtWidgets"))

    class QPointF:
        def __init__(self, x=0.0, y=0.0): self._x, self._y = x, y
        def x(self): return self._x
        def y(self): return self._y

    class QRectF:
        def __init__(self, x=0, y=0, w=0, h=0): self.a = (x, y, w, h)
        def left(self): return self.a[0]
        def top(self): return self.a[1]
        def right(self): return self.a[0] + self.a[2]
        def bottom(self): return self.a[1] + self.a[3]
        def width(self): return self.a[2]
        def height(self): return self.a[3]

    class QEvent:
        class Type(enum.Enum):
            GraphicsSceneMousePress = 1; GraphicsSceneMouseMove = 2
            GraphicsSceneMouseDoubleClick = 3; KeyPress = 4; GraphicsSceneMouseRelease = 5

    class QObject:
        def __init__(self, parent=None): pass

    class _Bound:
        def __init__(self): self.slots, self.emitted = [], []
        def connect(self, f): self.slots.append(f)
        def disconnect(self, f): self.slots.remove(f)
        def emit(self, *a):
            self.emitted.append(a)
            for s in self.slots: s(*a)

    class pyqtSignal:
        def __init__(self, *types_): pass
        def __set_name__(self, owner, name): self.name = "_sig_" + name
        def __get__(self, obj, owner=None):
            if obj is None: return self
            return obj.__dict__.setdefault(self.name, _Bound())

    class Qt:
        Key = enum.IntEnum("Key", dict(
            [("Key_Space", 32), ("Key_Slash", 47), ("Key_Escape", 0x1000000),
             ("Key_Backspace", 0x1000003), ("Key_Delete", 0x1000007), ("Key_Return", 0x1000004), ("Key_Enter", 0x1000005)]
            + [(f"Key_{c}", ord(c)) for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"]))

        class MouseButton(enum.Flag):
            NoButton = 0; LeftButton = 1; RightButton = 2
        class KeyboardModifier(enum.Flag):
            NoModifier = 0; ShiftModifier = 0x02000000; ControlModifier = 0x04000000; AltModifier = 0x08000000
        class BrushStyle(enum.Enum):
            NoBrush = 0; SolidPattern = 1
        class PenStyle(enum.Enum):
            SolidLine = 1; DashLine = 2

    class QColor:
        def __init__(self, *a): pass

    class QPen:
        def __init__(self, *a): pass
        def setCosmetic(self, b): pass
        def setStyle(self, s): pass

    class QPainterPath:
        def __init__(self): self.ops = []
        def moveTo(self, *a): self.ops.append(("M", a))
        def lineTo(self, *a): self.ops.append(("L", a))
        def quadTo(self, *a): self.ops.append(("Q", a))
        def cubicTo(self, *a): self.ops.append(("C", a))

    class _Brush:
        def __init__(self, style): self._s = style
        def style(self): return self._s

    class QGraphicsItem:
        def __init__(self): self._scene, self._vis, self._rect = None, True, None
        def setPen(self, p): pass
        def setZValue(self, z): pass
        def setAcceptedMouseButtons(self, b): pass
        def prepareGeometryChange(self): pass
        def update(self): pass
        def pos(self): return getattr(self, "_pos", QPointF(0, 0))
        def setPos(self, x, y=None):
            self._pos = x if y is None else QPointF(x, y)
        def mapFromScene(self, x):
            if isinstance(x, QPointF):
                return QPointF(x.x() - self.pos().x(), x.y() - self.pos().y())
            return x
        def mapToScene(self, x): return QPointF(x.x() + self.pos().x(), x.y() + self.pos().y())
        def setSelected(self, on):
            self._sel = on
            if self._scene is not None: self._scene.selectionChanged.emit()
        def isSelected(self): return getattr(self, "_sel", False)
        def setVisible(self, v): self._vis = v
        def isVisible(self): return self._vis
        def parentItem(self): return None
        def scene(self): return self._scene
        def sceneBoundingRect(self):
            r, p = self._rect, getattr(self, "_pos", None)
            if r is None or p is None: return r
            return QRectF(r.left() + p.x(), r.top() + p.y(), r.width(), r.height())
        def brush(self): return _Brush(Qt.BrushStyle.SolidPattern)

    class QGraphicsLineItem(QGraphicsItem): pass

    class QGraphicsPathItem(QGraphicsItem):
        def __init__(self, path=None): super().__init__(); self.path = path
        def setPath(self, p): self.path = p
        def brush(self): return _Brush(Qt.BrushStyle.NoBrush)

    class QGraphicsRectItem(QGraphicsItem):
        def __init__(self, x, y, w, h): super().__init__(); self._rect = QRectF(x, y, w, h)

    class QGraphicsScene:
        selectionChanged = pyqtSignal()
        def __init__(self, *a): self._items, self.filters = [], []
        def selectedItems(self): return [i for i in self._items if i.isSelected()]
        def removeItem(self, it): self._items.remove(it); it._scene = None
        def addItem(self, it): it._scene = self; self._items.append(it)
        def items(self): return list(self._items)
        def views(self): return []
        def installEventFilter(self, f): self.filters.append(f)
        def removeEventFilter(self, f): self.filters.remove(f)

    core.QPointF, core.QRectF, core.QEvent, core.QObject = QPointF, QRectF, QEvent, QObject
    core.pyqtSignal, core.Qt = pyqtSignal, Qt
    gui.QColor, gui.QPen, gui.QPainterPath = QColor, QPen, QPainterPath
    for c in (QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene):
        setattr(wid, c.__name__, c)
    pkg = types.ModuleType("PyQt6")
    pkg.QtCore, pkg.QtGui, pkg.QtWidgets = core, gui, wid
    sys.modules.update({"PyQt6": pkg, "PyQt6.QtCore": core, "PyQt6.QtGui": gui, "PyQt6.QtWidgets": wid})
