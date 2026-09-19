"""smartline demo - run:  python examples/demo.py   (QT_API=pyside6 ... to force a binding)

Click to anchor, move, click to place legs, double-click / Enter to finish.
Space or "/" flips the posture, M or Shift+Space changes mode, 1-8 picks one, G cycles the bus guide,
Backspace undoes a leg, Esc cancels, right-click ends at the last placed point.
"""
import sys

from smartline.qt_compat import API, Qt, QtCore, QtGui, QtWidgets, enum, exec_app
from smartline.tool import SmartLineTool


def build_scene() -> QtWidgets.QGraphicsScene:
    scene = QtWidgets.QGraphicsScene(0, 0, 1000, 650)
    fill = QtGui.QBrush(QtGui.QColor(225, 232, 245))
    pen = QtGui.QPen(QtGui.QColor(70, 90, 130), 1.5)
    movable = enum(QtWidgets.QGraphicsItem, "GraphicsItemFlag", "ItemIsMovable")
    for x, y, w, h in [(140, 90, 160, 90), (420, 60, 120, 200), (640, 140, 200, 80),
                       (230, 330, 180, 120), (520, 380, 140, 140), (760, 330, 120, 220)]:
        it = scene.addRect(QtCore.QRectF(0, 0, w, h), pen, fill)
        it.setPos(x, y)
        it.setFlag(movable, True)
    el = scene.addEllipse(QtCore.QRectF(0, 0, 110, 110), pen, fill)
    el.setPos(60, 430)
    el.setFlag(movable, True)
    return scene


class Window(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"smartline demo  [{API}]")
        self.scene = build_scene()
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setRenderHint(enum(QtGui.QPainter, "RenderHint", "Antialiasing"), True)
        self.setCentralWidget(self.view)

        self.tool = SmartLineTool(self.scene)
        self.tool.corner_radius = 8
        self.tool.grid = 10

        bar = self.addToolBar("Modes")
        self.draw_btn = QtWidgets.QToolButton()
        self.draw_btn.setText("Draw")
        self.draw_btn.setCheckable(True)
        self.draw_btn.toggled.connect(self.tool.set_active)
        bar.addWidget(self.draw_btn)
        self.combo = QtWidgets.QComboBox()
        for i, r in enumerate(self.tool.routers, 1):
            self.combo.addItem(f"{i}  {r.label}", r.name)
        self.combo.currentIndexChanged.connect(self.tool.set_mode)
        bar.addWidget(self.combo)
        self.clear = QtWidgets.QDoubleSpinBox()
        self.clear.setPrefix("clearance ")
        self.clear.setRange(0, 60)
        self.clear.setValue(self.tool.clearance)
        self.clear.valueChanged.connect(lambda v: setattr(self.tool, "clearance", v))
        bar.addWidget(self.clear)

        self.tool.modeChanged.connect(self._sync)
        self.tool.postureChanged.connect(lambda _f: self._sync())
        self.tool.routeFinished.connect(
            lambda route: self.statusBar().showMessage(f"finished: {route.to_svg()}"[:160], 4000))
        self.draw_btn.setChecked(True)
        self._sync()
        self.view.setFocus()

    def _sync(self, *_):
        idx = self.tool.routers.index(self.tool.router)
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(idx)
        self.combo.blockSignals(False)
        self.statusBar().showMessage(
            f"mode: {self.tool.router.label}   |   Space=/ flip   M=next mode   "
            f"Backspace=undo leg   Enter/dbl-click=finish   Esc=cancel")


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = Window()
    w.resize(1100, 760)
    w.show()
    if "--smoke" in sys.argv:                 # CI: build the window, draw one line, quit
        t = w.tool
        t.set_mode("hug")
        t._anchor, t._cursor = (20.0, 20.0), (900.0, 600.0)
        t.refresh_obstacles(); t.refresh_guides(); t._reroute(); t.finish()
        QtCore.QTimer.singleShot(200, app.quit)
    sys.exit(exec_app(app))
