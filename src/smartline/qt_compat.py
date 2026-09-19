"""Tiny Qt binding shim: PyQt6, PySide6, PyQt5 or PySide2 - whichever is there.

Resolution order:
1. a binding that the host application has *already imported* (so smartline
   never drags a second Qt into your process),
2. the ``QT_API`` environment variable (``pyqt6``/``pyside6``/``pyqt5``/``pyside2``),
3. first importable of PyQt6, PySide6, PyQt5, PySide2.
"""
from __future__ import annotations

import importlib
import os
import sys

_CANDIDATES = ["PyQt6", "PySide6", "PyQt5", "PySide2"]


def _pick() -> str:
    for name in _CANDIDATES:
        if name + ".QtCore" in sys.modules:
            return name
    want = os.environ.get("QT_API", "").strip().lower()
    for name in _CANDIDATES:
        if name.lower() == want:
            return name
    for name in _CANDIDATES:
        try:
            importlib.import_module(name + ".QtCore")
            return name
        except ImportError:
            continue
    raise ImportError("smartline needs one of: " + ", ".join(_CANDIDATES))


API = _pick()
QtCore = importlib.import_module(API + ".QtCore")
QtGui = importlib.import_module(API + ".QtGui")
QtWidgets = importlib.import_module(API + ".QtWidgets")

Signal = getattr(QtCore, "Signal", None) or getattr(QtCore, "pyqtSignal")
Qt = QtCore.Qt


def enum(owner, scope: str, member: str):
    """``enum(Qt, "Key", "Key_Escape")`` -> works with scoped (Qt6) and
    unscoped (Qt5) enum spellings alike."""
    scoped = getattr(owner, scope, None)
    if scoped is not None and hasattr(scoped, member):
        return getattr(scoped, member)
    return getattr(owner, member)


def exec_app(app) -> int:
    return app.exec() if hasattr(app, "exec") else app.exec_()
