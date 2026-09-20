# Changelog

## 0.4.1
- Re-routing keeps lines off each other: lane search with `wire_spacing`, per-obstacle `margins`,
  cuts at hull entry/exit, `geometry.overlap_length`, taut detours for free-angle routers
  (`Router.free_angle`, `geometry.shortcut`), `meta["overlaps"]`.

## 0.4.0
- `SmartLineTool.reroute_around(shape)` / `reroute_colliding()` and pure-Python `smartline.reroute`:
  repair lines a shape now overlaps, leg by leg, with the routing method each leg was drawn with.
- `Route.legs()` provenance (router, posture, flip per drawn leg); `Router.avoiding()`.
- `RouteEditor.refresh()`; tool hooks `route_of` / `apply_route`, `wires()`, `set_item_route()`.

## 0.3.0
- `smartline.edit`: pure-Python editing operations on a Route (move vertex / section / control point,
  insert, delete, convert, normalize) that keep orthogonal routes orthogonal and cubic joins smooth.
- `RouteEditor`: select-and-reshape editor with anchor, section and tangent-line control handles.
- `app.py`: PyQt6 test bench. `SmartLineTool.previewChanged` signal (live leg on every update).

## 0.2.0
- `Route` result type (L/Q/C segments) with `flatten`, `to_segments`, `to_nodes`, `to_svg`.
- Two-level router contract: `route()` skeleton + `shape()` drawn geometry.
- `CubicRouter` (S-curve, corner blends, Catmull-Rom) with `smooth()` as the override point.
- Router registry: `register` / `create` / `available`, `replace=True` overrides,
  `smartline.routers` entry-point plugins, `SmartLineTool(scene, modes=[...])`.
- Tool: `routeFinished` signal, editable `keymap`, `make_item` / `collect_obstacles` /
  `collect_guides` override points. `item_factory` now receives a `Route`.
- `BusRouter` - follow an existing net at a fixed pitch.
- src layout, `pyproject.toml`, binding extras, CI matrix.

## 0.1.0
- Straight, orthogonal, hug (walkaround), auto-avoid (A*), octilinear routers; `SmartLineTool`.
