"""smartline - smart rubber-band line drawing for PyQt / PySide QGraphicsScenes.

The routing core (``geometry``, ``route``, ``routers``, ``registry``) is pure
Python.  Qt is only imported when you touch ``SmartLineTool`` and friends.
"""
from .registry import (DEFAULT_MODES, available, create, default_routers,
                       load_plugins, register, unregister)
from . import edit, reroute, tidy
from .route import Route, Seg
from .routers import (AvoidRouter, BusRouter, CubicRouter, HugRouter,
                      OctilinearRouter, OrthoRouter, RouteContext, Router,
                      StraightRouter, blend_corners, catmull_rom)

__version__ = "0.5.0"
__all__ = ["AvoidRouter", "BusRouter", "CubicRouter", "HugRouter", "OctilinearRouter",
           "OrthoRouter", "RouteContext", "Router", "StraightRouter", "blend_corners", "catmull_rom",
           "Route", "Seg", "edit", "reroute", "tidy", "DEFAULT_MODES", "available", "create", "default_routers",
           "load_plugins", "register", "unregister"]

_QT_NAMES = ("SmartLineTool", "route_to_path", "rounded_path", "polyline_path")


def __getattr__(name):            # lazy: keep the core importable without Qt
    if name in _QT_NAMES:
        from . import tool
        return getattr(tool, name)
    if name == "RouteEditor":
        from .editor import RouteEditor
        return RouteEditor
    raise AttributeError(f"module 'smartline' has no attribute {name!r}")
