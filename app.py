"""smartline test bench (PyQt6).

    pip install -e ".[pyqt6]"
    python app.py

examples/demo.py is the minimal "how do I embed this" sample.  This file is the
bench for *trying* the routers: every mode, every tuning knob, ports to snap to,
movable shapes, clearance-hull overlay, and a live read-out of the route that
the current mode is producing.

Canvas
    D / E            Draw lines  /  Select-Edit lines   (the two toolbar buttons)
Select-Edit mode - click a wire to edit it (RouteEditor); drag shapes; Delete
    squares / bars   drag a vertex / a whole straight section (orthogonal wires stay orthogonal)
    circles          cubic control points, joined to their anchor by a tangent line
    double-click     on the wire: add a vertex      on a vertex: remove it
    C / L            section under the cursor -> cubic / line
    Shift / Alt / Ctrl while dragging: free move / break tangent / mirror tangent
    R                re-route every line that overlaps the selected shape(s)  (also automatic on drop)
    Ctrl+Z           undo the last edit / re-route
    wheel            zoom            middle-drag / Select-mode drag on empty space: pan
While drawing  (same keys as the library's DEFAULT_KEYMAP)
    click            anchor / commit leg        double-click, Enter   finish
    right-click      finish at last commit      Esc cancel   Backspace undo leg
    Space or /       flip posture               M / Shift+M  next / previous mode
    1..9             pick mode                  G            bus: cycle guide net
    Shift            constrain straight legs to 45 degrees
"""
from __future__ import annotations

import math
import sys

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QAction, QActionGroup, QBrush, QColor, QKeySequence, QPainter,
                         QPainterPathStroker, QPen, QPolygonF, QTextOption)
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDockWidget, QDoubleSpinBox,
                             QFormLayout, QGraphicsEllipseItem, QGraphicsItem,
                             QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene,
                             QGraphicsView, QLabel, QListWidget, QMainWindow,
                             QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

import smartline as sl
from smartline import geometry as g
from smartline.editor import RouteEditor
from smartline.tool import SmartLineTool, route_to_path

WIRE_COLORS = ["#1f6fd0", "#2a9d5c", "#8a3fd0", "#d0571f", "#0f8b8d", "#b8336a"]
PORT_SNAP_PX = 14.0


# ----------------------------------------------------------------- scene items

class Port(QGraphicsEllipseItem):
    """Connection point.  It is a *child* of its node, so the tool's default
    obstacle scan (top-level items only) ignores it."""

    def __init__(self, parent: QGraphicsItem, x: float, y: float):
        super().__init__(-4, -4, 8, 8, parent)
        self.setPos(x, y)
        self.setBrush(QBrush(QColor("#ffffff")))
        self.setPen(QPen(QColor("#465a82"), 1.5))
        self.setZValue(2)


class Node(QGraphicsRectItem):
    """Movable, selectable shape with a port in the middle of each side."""

    def __init__(self, x: float, y: float, w: float, h: float, label: str = ""):
        super().__init__(0, 0, w, h)
        self.setPos(x, y)
        self.setBrush(QBrush(QColor(225, 232, 245)))
        self.setPen(QPen(QColor(70, 90, 130), 1.5))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.label = label
        self.ports = [Port(self, w / 2, 0), Port(self, w, h / 2),
                      Port(self, w / 2, h), Port(self, 0, h / 2)]

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.label:
            painter.setPen(QColor(70, 90, 130))
            painter.drawText(self.rect(), self.label, QTextOption(Qt.AlignmentFlag.AlignCenter))

    on_dropped = None            # set by the bench: f(node) after a drag ends

    def mousePressEvent(self, ev):
        self._press_pos = self.pos()
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        if Node.on_dropped and self.pos() != getattr(self, "_press_pos", self.pos()):
            Node.on_dropped(self)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self.scene():
            self.scene().update()                # hull overlay follows the shape
        return super().itemChange(change, value)


class Wire(QGraphicsPathItem):
    """Finished connector.  Unfilled on purpose: that is how the tool's default
    scan tells wires (bus guides, not obstacles) from shapes."""

    def __init__(self, route: sl.Route, color: str, corner_radius: float):
        super().__init__(route_to_path(route, corner_radius))
        pen = QPen(QColor(color), 2.2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setZValue(1)
        self.route = route

    def shape(self):
        """An open path has no area, so by default only pixel-perfect clicks (or
        clicks inside a bend) would select it.  Give it a 12 px wide hit zone."""
        stroker = QPainterPathStroker()
        stroker.setWidth(12.0)
        return stroker.createStroke(self.path())

    def boundingRect(self):
        return self.shape().boundingRect().adjusted(-2, -2, 2, 2)

    def paint(self, painter, option, widget=None):
        if self.isSelected():                    # soft halo instead of Qt's dashed box
            halo = QPen(QColor(255, 176, 0, 110), 7.0)
            halo.setCapStyle(Qt.PenCapStyle.RoundCap)
            halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(halo)
            painter.drawPath(self.path())
        painter.setPen(self.pen())
        painter.drawPath(self.path())


class BenchScene(QGraphicsScene):
    """Dotted grid behind, clearance hulls in front."""

    def __init__(self):
        super().__init__(0, 0, 1400, 900)
        self.tool: SmartLineTool | None = None
        self.show_hulls = True
        self.grid_step = 20.0

    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, QColor("#fbfcfe"))
        step = self.grid_step
        if step < 4:
            return
        painter.setPen(QPen(QColor("#d5dbe6"), 0))
        x0 = math.floor(rect.left() / step) * step
        y0 = math.floor(rect.top() / step) * step
        pts = []
        y = y0
        while y <= rect.bottom():
            x = x0
            while x <= rect.right():
                pts.append(QPointF(x, y))
                x += step
            y += step
        if len(pts) < 40000:
            painter.drawPoints(QPolygonF(pts))

    def drawForeground(self, painter: QPainter, rect: QRectF):
        if not (self.show_hulls and self.tool):
            return
        c = self.tool.clearance
        pen = QPen(QColor(120, 135, 165, 170), 0, Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for r in self.tool.collect_obstacles():
            painter.drawRect(r.adjusted(-c, -c, c, c))


class BenchView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._panning = False
        self._pan_from = None

    def wheelEvent(self, ev):
        factor = 1.15 ** (ev.angleDelta().y() / 120.0)
        zoom = self.transform().m11() * factor
        if 0.15 < zoom < 8.0:
            self.scale(factor, factor)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.MiddleButton:
            self._panning, self._pan_from = True, ev.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._panning:
            d = ev.position() - self._pan_from
            self._pan_from = ev.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.unsetCursor()
            return
        super().mouseReleaseEvent(ev)


# ---------------------------------------------------------------------- window

class Bench(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"smartline {sl.__version__} - test bench (PyQt6)")
        self.scene = BenchScene()
        self.view = BenchView(self.scene)
        self.setCentralWidget(self.view)
        self._wire_count = 0

        # every registered mode, default ring first
        modes = list(sl.DEFAULT_MODES) + [m for m in sl.available() if m not in sl.DEFAULT_MODES]
        self.tool = SmartLineTool(self.scene, modes=modes)
        self.tool.grid = 10.0
        self.tool.corner_radius = 8.0
        self.tool.snap_provider = self._snap_to_port
        self.tool.item_factory = self._make_wire
        self.scene.tool = self.tool

        self.editor = RouteEditor(self.scene)
        self.editor.grid = self.tool.grid
        self.editor.corner_radius = self.tool.corner_radius
        self.editor.snap_provider = self._snap_to_port
        self.editor.routeEdited.connect(self._on_edited)
        self.editor.editingStarted.connect(lambda _it: self._status())
        self.editor.editingStopped.connect(lambda _it: self._status())
        self._undo = []                          # batches of (item, previous Route)
        self.tool.routesRerouted.connect(self._on_rerouted)
        Node.on_dropped = self._on_node_dropped

        self._populate()
        self._build_mode_dock()
        self._build_settings_dock()
        self._build_log_dock()
        self._build_actions()

        self.tool.modeChanged.connect(self._on_mode)
        self.tool.postureChanged.connect(lambda _f: self._status())
        self.tool.previewChanged.connect(self._on_preview)
        self.tool.routeFinished.connect(self._on_finished)
        self.tool.cancelled.connect(lambda: self._status("cancelled"))
        self.editor.routeChanged.connect(lambda _it, r: self._on_preview(r))

        self.draw_action.setChecked(True)
        self._on_mode(self.tool.mode)
        self.view.setFocus()

    # ------------------------------------------------------------- scene setup
    def _populate(self):
        for it in list(self.scene.items()):
            if isinstance(it, (Node, Wire)):
                self.scene.removeItem(it)
        self.tool._wire_items.clear()
        self._nodes = []
        layout = [(140, 110, 170, 90, "A"), (470, 70, 120, 210, "B"), (760, 150, 210, 80, "C"),
                  (240, 380, 190, 120, "D"), (590, 430, 150, 150, "E"), (900, 380, 130, 240, "F"),
                  (1120, 120, 150, 110, "G"), (120, 640, 160, 100, "H")]
        for x, y, w, h, label in layout:
            self._add_node(x, y, w, h, label)
        self.scene.update()

    def _add_node(self, x, y, w, h, label=""):
        n = Node(x, y, w, h, label)
        self.scene.addItem(n)
        self._nodes.append(n)            # keep Python references (matters on PySide)
        return n

    def _add_node_at_center(self):
        c = self.view.mapToScene(self.view.viewport().rect().center())
        label = chr(ord("A") + len(self._nodes) % 26)
        self._add_node(c.x() - 70, c.y() - 45, 140, 90, label)
        self.scene.update()

    # ------------------------------------------------------------ tool hooks
    def _snap_to_port(self, p: QPointF):
        if not self.snap_ports.isChecked():
            return None
        best, best_d = None, PORT_SNAP_PX
        for n in self._nodes:
            if n.scene() is None:
                continue
            for port in n.ports:
                q = port.scenePos()
                d = math.hypot(q.x() - p.x(), q.y() - p.y())
                if d < best_d:
                    best, best_d = q, d
        return best

    def _make_wire(self, route: sl.Route):
        color = WIRE_COLORS[self._wire_count % len(WIRE_COLORS)]
        self._wire_count += 1
        return Wire(route, color, self.tool.corner_radius)

    # ------------------------------------------------------------------ docks
    def _build_mode_dock(self):
        self.mode_list = QListWidget()
        self.mode_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # keys stay with the canvas
        for i, r in enumerate(self.tool.routers, 1):
            key = f"{i}  " if i <= 9 else "    "
            self.mode_list.addItem(f"{key}{r.label}   [{r.name}]")
        self.mode_list.setMinimumHeight(10 * 20)
        self.mode_list.currentRowChanged.connect(self._pick_mode)
        self.mode_help = QLabel()
        self.mode_help.setWordWrap(True)
        self.mode_help.setStyleSheet("color:#465a82; padding:4px;")
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.mode_list, 1)
        lay.addWidget(self.mode_help)
        dock = QDockWidget("Routing mode", self)
        dock.setWidget(box)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def _spin(self, lo, hi, value, step, slot, suffix=" px"):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(0)
        s.setSuffix(suffix)
        s.setValue(value)
        s.valueChanged.connect(slot)
        return s

    def _build_settings_dock(self):
        form_host = QWidget()
        form = QFormLayout(form_host)
        t = self.tool
        form.addRow("Clearance", self._spin(0, 80, t.clearance, 2, self._set("clearance")))
        form.addRow("Bend penalty", self._spin(0, 400, t.bend_penalty, 10, self._set("bend_penalty")))
        form.addRow("Line spacing", self._spin(0, 60, t.wire_spacing, 2, self._set("wire_spacing")))
        form.addRow("Bus pitch", self._spin(4, 80, t.bus_pitch, 2, self._set("bus_pitch")))
        form.addRow("Bus capture", self._spin(10, 400, t.bus_capture, 10, self._set("bus_capture")))
        form.addRow("Grid snap", self._spin(0, 100, t.grid, 5, self._set_grid))
        form.addRow("Corner radius", self._spin(0, 60, t.corner_radius, 2, self._set("corner_radius")))
        self.snap_ports = QCheckBox("Snap to ports")
        self.snap_ports.setChecked(True)
        form.addRow(self.snap_ports)
        hulls = QCheckBox("Show clearance hulls")
        hulls.setChecked(True)
        hulls.toggled.connect(self._toggle_hulls)
        form.addRow(hulls)
        self.auto_reroute = QCheckBox("Re-route lines when a shape is dropped")
        self.auto_reroute.setChecked(True)
        form.addRow(self.auto_reroute)
        for text, slot in (("Flip posture  (Space)", t.toggle_posture),
                           ("Re-route around selected shapes  (R)", self._reroute_selected),
                           ("Add shape", self._add_node_at_center),
                           ("Clear wires", self._clear_wires),
                           ("Reset scene", self._populate)):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            form.addRow(b)
        dock = QDockWidget("Settings", self)
        dock.setWidget(form_host)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def _build_log_dock(self):
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        self.log.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 11px;")
        dock = QDockWidget("Finished routes  (Route.to_svg / to_nodes)", self)
        dock.setWidget(self.log)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    def _build_actions(self):
        bar = self.addToolBar("Canvas")
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        group = QActionGroup(self)
        group.setExclusive(True)
        self.draw_action = QAction("✏  Draw lines  (D)", self)
        self.edit_action = QAction("⬚  Select / Edit lines  (E)", self)
        for act, keys in ((self.draw_action, "D"), (self.edit_action, "E")):
            act.setCheckable(True)
            act.setShortcut(QKeySequence(keys))
            group.addAction(act)
            bar.addAction(act)
        self.draw_action.toggled.connect(self._set_drawing)
        bar.addSeparator()
        delete = QAction("Delete selected", self)
        delete.setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))
        delete.triggered.connect(self._delete_selected)
        bar.addAction(delete)
        undo = QAction("Undo edit", self)
        undo.setShortcut(QKeySequence(QKeySequence.StandardKey.Undo))
        undo.triggered.connect(self._undo_edit)
        bar.addAction(undo)
        rr = QAction("Re-route", self)
        rr.setShortcut(QKeySequence("R"))
        rr.triggered.connect(self._reroute_selected)
        bar.addAction(rr)
        fit = QAction("Fit", self)
        fit.setShortcut(QKeySequence("F"))
        fit.triggered.connect(lambda: self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40),
                                                          Qt.AspectRatioMode.KeepAspectRatio))
        bar.addAction(fit)

    # ------------------------------------------------------------------ slots
    def _set(self, attr):
        def apply(v):
            setattr(self.tool, attr, float(v))
            self.tool.refresh_obstacles()
            self.tool._reroute()                 # live preview follows the knob
            self.scene.update()
        return apply

    def _set_grid(self, v):
        self.tool.grid = self.editor.grid = float(v)
        self.scene.grid_step = float(v) * 2 if v else 0.0
        self.scene.update()

    def _toggle_hulls(self, on):
        self.scene.show_hulls = on
        self.scene.update()

    def _set_drawing(self, on):
        self.tool.set_active(on)
        self.editor.corner_radius = self.tool.corner_radius
        self.editor.set_active(not on)           # draw and edit are mutually exclusive
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag if on
                              else QGraphicsView.DragMode.RubberBandDrag)
        self.view.viewport().setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)
        self._status()

    def _pick_mode(self, row):
        if row >= 0:
            self.tool.set_mode(row)
            self.view.setFocus()

    def _on_mode(self, _name):
        row = self.tool.routers.index(self.tool.router)
        self.mode_list.blockSignals(True)
        self.mode_list.setCurrentRow(row)
        self.mode_list.blockSignals(False)
        doc = (type(self.tool.router).__doc__ or "").strip().split("\n\n")[0]
        self.mode_help.setText(" ".join(doc.split()))
        self._status()

    def _on_preview(self, route):
        if route is None:
            return
        flat = route.flatten()
        kind = "polyline" if route.is_polyline else "curve"
        self._status(f"live: {kind}, {len(route.segs)} seg, {g.bends(flat) if route.is_polyline else '-'} bends, "
                     f"{g.length(flat):.0f} px")

    def _on_finished(self, route: sl.Route):
        self.log.appendPlainText(f"[{route.meta.get('router', self.tool.mode)}]  {route.to_svg(1)}")
        self.log.appendPlainText(f"    nodes: {[n['cmd'] for n in route.to_nodes(hv=True)]}   "
                                 f"segments: {len(route.to_segments())}   bbox: "
                                 f"{tuple(round(v) for v in route.bbox())}")
        self.scene.update()

    def _status(self, extra: str = ""):
        if self.tool.is_active():
            flip = "flipped" if self.tool._flip else "default"
            msg = (f"DRAW   |   mode: {self.tool.router.label}   |   posture: {flip}"
                   f"   |   press E to select / edit lines")
        elif self.editor.is_editing():
            msg = ("EDIT   |   drag squares (vertices), bars (sections), circles (curve handles)   |   "
                   "double-click: add / remove vertex   |   C / L: section to cubic / line   |   Ctrl+Z undo")
        else:
            msg = "SELECT   |   click a line to edit it, drag shapes to move them   |   press D to draw"
        self.statusBar().showMessage(msg + (f"   |   {extra}" if extra else ""))

    def _clear_wires(self):
        for it in list(self.scene.items()):
            if isinstance(it, Wire):
                self.scene.removeItem(it)
        self.tool._wire_items.clear()
        self.scene.update()

    def _on_edited(self, item, old, new):
        self._undo.append([(item, old)])
        self.log.appendPlainText(f"[edit]  {new.to_svg(1)}")

    # ---- re-routing: the library call is tool.reroute_around(shape) ----------
    def _on_node_dropped(self, node):
        if self.auto_reroute.isChecked():
            self.tool.reroute_around(node)

    def _reroute_selected(self):
        shapes = [it for it in self.scene.selectedItems() if isinstance(it, Node)]
        if not shapes:
            self._status("select one or more shapes first")
            return
        total = sum(len(self.tool.reroute_around(n)) for n in shapes)
        if not total:
            self._status("no line overlaps the selected shape(s)")

    def _on_rerouted(self, changes):
        self._undo.append([(item, old) for item, old, _new in changes])
        for _item, old, new in changes:
            methods = sorted({str(l["router"]) for l in old.legs()})
            flag = ("  UNRESOLVED" if new.meta.get("unresolved") else "") + \
                   (f"  SHARES {new.meta['overlaps']} px OF TRACK" if new.meta.get("overlaps") else "")
            self.log.appendPlainText(f"[re-route as {'+'.join(methods)}]{flag}  {new.to_svg(1)}")
        self.editor.refresh()
        self.scene.update()
        self._status(f"re-routed {len(changes)} line(s)   -   Ctrl+Z to undo")

    def _undo_edit(self):
        while self._undo:
            batch = [(item, old) for item, old in self._undo.pop() if item.scene() is self.scene]
            if not batch:                        # those wires were deleted since
                continue
            for item, old in batch:
                self.tool.set_item_route(item, old)
            self.editor.refresh()
            self.scene.update()
            return

    def _delete_selected(self):
        if self.editor.delete_hot_anchor():      # a vertex under the cursor wins over the wire
            return
        self.editor.stop()
        for it in self.scene.selectedItems():
            self.scene.removeItem(it)
            if it in self._nodes:
                self._nodes.remove(it)
        self.scene.update()

    # ------------------------------------------------------------------- smoke
    def smoke(self):
        """Draw one connector per mode without a mouse - used by CI."""
        a, b = (60.0, 60.0), (1300.0, 820.0)
        for i, r in enumerate(self.tool.routers):
            self.tool.set_mode(i)
            self.tool._anchor = (a[0], a[1] + 14.0 * i)
            self.tool._cursor = (b[0], b[1] - 14.0 * i)
            self.tool.refresh_obstacles()
            self.tool.refresh_guides()
            self.tool._reroute()
            self.tool.toggle_posture()
            self.tool.finish()
        wires = [it for it in self.scene.items() if isinstance(it, Wire)]
        assert len(wires) == len(self.tool.routers), (len(wires), len(self.tool.routers))
        # editor: select a wire, reshape it, undo
        self.edit_action.setChecked(True)
        target = max((w for w in wires if w._smartline_route.is_polyline),
                     key=lambda w: len(w._smartline_route.segs))
        target.setSelected(True)
        assert self.editor.is_editing() and self.editor.item() is target
        before = self.editor.route()
        self.editor.set_route(sl.edit.convert_segment(sl.edit.insert_anchor(before, 0, 0.5), 0, "C"))
        assert not self.editor.route().is_polyline and len(self._undo) == 1
        self._undo_edit()
        assert self.editor.route().to_svg() == before.to_svg()
        # re-route: drop a shape onto the lines, repair, undo
        blocker = self._add_node(620, 300, 120, 400, "X")
        n_before = len(self._undo)
        changes = self.tool.reroute_around(blocker)
        assert changes and len(self._undo) == n_before + 1, (len(changes), len(self._undo))
        rect = blocker.sceneBoundingRect()
        rect = (rect.left(), rect.top(), rect.right(), rect.bottom())
        unresolved = [new for _i, _o, new in changes if sl.reroute.hits(new, rect)]
        assert len(unresolved) <= len(changes) // 2, f"{len(unresolved)} of {len(changes)} still blocked"
        self._undo_edit()
        assert all(self.tool.route_of(i).to_svg() == o.to_svg() for i, o, _n in changes)
        flats = [self.tool.route_of(w).flatten() for w in wires]
        news = [n.flatten() for _i, _o, n in changes if not n.meta.get("overlaps")]
        shared = max((g.overlap_length(a, b, self.tool.wire_spacing / 2) for a in news for b in flats
                      if a is not b and a != b), default=0.0)
        print(f"smoke: worst shared track after re-route = {shared:.1f} px")
        print(f"smoke: re-routed {len(changes)} lines ({len(unresolved)} unresolved), undo ok")
        print(f"smoke ok: {len(wires)} wires, modes = {[r.name for r in self.tool.routers]}")


def main() -> int:
    app = QApplication(sys.argv)
    w = Bench()
    w.resize(1500, 950)
    w.show()
    if "--smoke" in sys.argv:
        w.smoke()
        QTimer.singleShot(300, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
