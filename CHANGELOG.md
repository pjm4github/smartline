# Changelog

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
