"""Route - the one result type every consumer reads.

A route is an SVG-path-like chain:  a start point plus ``L`` (line), ``Q``
(quadratic) and ``C`` (cubic) segments.  Polyline routers produce only ``L``;
curve routers produce ``C``/``Q``.  Everything downstream is a *view* of it:

    route.anchors()        on-curve vertices            (node editing)
    route.flatten()        polyline approximation       (hit-testing, obstacles, bus guides)
    route.to_segments()    [(p0, p1), ...]              (KiCad wires, netlists)
    route.to_nodes()       [{"cmd": "M", "x":..}, ...]  (PictoSync curve nodes, JSON)
    route.to_svg()         "M 0 0 L 10 0 C ..."         (SVG / debugging)

Pure Python - no Qt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from . import geometry as g
from .geometry import Pt

_ARITY = {"L": 1, "Q": 2, "C": 3}


@dataclass(frozen=True)
class Seg:
    """One path segment.  ``pts`` = (end,) for L, (ctrl, end) for Q,
    (ctrl1, ctrl2, end) for C."""
    cmd: str
    pts: Tuple[Pt, ...]

    def __post_init__(self):
        if self.cmd not in _ARITY or len(self.pts) != _ARITY[self.cmd]:
            raise ValueError(f"bad segment {self.cmd!r} with {len(self.pts)} points")

    @property
    def end(self) -> Pt:
        return self.pts[-1]


@dataclass
class Route:
    start: Pt
    segs: List[Seg] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)   # router name, guide, lane ...

    # ------------------------------------------------------------ construction
    @classmethod
    def from_points(cls, pts: Sequence[Pt], **meta) -> "Route":
        pts = [(float(x), float(y)) for x, y in pts]
        return cls(pts[0], [Seg("L", (p,)) for p in pts[1:]], dict(meta))

    @classmethod
    def coerce(cls, value) -> "Route":
        """Accept a Route or a plain point list (what simple routers return)."""
        return value if isinstance(value, Route) else cls.from_points(value)

    def line_to(self, p: Pt) -> "Route":
        self.segs.append(Seg("L", (p,)))
        return self

    def quad_to(self, c: Pt, p: Pt) -> "Route":
        self.segs.append(Seg("Q", (c, p)))
        return self

    def cubic_to(self, c1: Pt, c2: Pt, p: Pt) -> "Route":
        self.segs.append(Seg("C", (c1, c2, p)))
        return self

    def joined(self, other: "Route") -> "Route":
        """self followed by other (other.start should equal self.end)."""
        segs = list(self.segs)
        if g.dist(self.end, other.start) > 1e-6:
            segs.append(Seg("L", (other.start,)))
        meta = {**self.meta, **other.meta, "legs": self.legs() + other.legs()}
        return Route(self.start, segs + list(other.segs), meta)

    def legs(self) -> List[Dict[str, Any]]:
        """Provenance of each drawn leg: ``{"router", "flip", "posture", "end"}``.

        A connector can be drawn in several legs with different modes; this is
        what lets :mod:`smartline.reroute` repair each part with the routing
        method it was drawn with.  ``end`` is the point where the leg stops.
        """
        if "legs" in self.meta:
            return [dict(l) for l in self.meta["legs"]]
        return [{"router": self.meta.get("router"), "flip": bool(self.meta.get("flip", False)),
                 "posture": self.meta.get("posture"), "end": self.end}]

    # ------------------------------------------------------------------ views
    @property
    def end(self) -> Pt:
        return self.segs[-1].end if self.segs else self.start

    @property
    def is_polyline(self) -> bool:
        return all(s.cmd == "L" for s in self.segs)

    def anchors(self) -> List[Pt]:
        return [self.start] + [s.end for s in self.segs]

    def flatten(self, tolerance: float = 0.5) -> List[Pt]:
        """Polyline approximation; curves are subdivided so that the chord error
        stays around *tolerance* scene units."""
        out, cur = [self.start], self.start
        for s in self.segs:
            if s.cmd == "L":
                out.append(s.end)
            else:
                ctrl = (cur,) + s.pts
                span = sum(g.dist(ctrl[i], ctrl[i + 1]) for i in range(len(ctrl) - 1))
                n = max(4, min(96, int(math.ceil(math.sqrt(span / max(tolerance, 1e-3)) * 1.5))))
                out.extend(_bezier(ctrl, k / n) for k in range(1, n + 1))
            cur = s.end
        return g.simplify(out) if self.is_polyline else out

    def to_segments(self, tolerance: float = 0.5) -> List[Tuple[Pt, Pt]]:
        pts = self.flatten(tolerance)
        return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]

    def bbox(self) -> Tuple[float, float, float, float]:
        """(left, top, right, bottom) of the control polygon (a safe superset)."""
        pts = [self.start] + [p for s in self.segs for p in s.pts]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def to_nodes(self, normalize: bool = False, hv: bool = False) -> List[Dict[str, Any]]:
        """SVG-style node dicts: ``cmd, x, y`` plus ``c1x,c1y,c2x,c2y`` (C) or
        ``cx,cy`` (Q) - the schema of PictoSync's curve items.

        normalize  coordinates become 0..1 fractions of :meth:`bbox`
                   (pair it with ``bbox()`` for the item's position and size).
        hv         emit axis-aligned lines as ``H``/``V`` nodes (orthocurve).
        """
        l, t, r, b = self.bbox()
        w, h = max(r - l, 1e-9), max(b - t, 1e-9)

        def cv(p: Pt) -> Pt:
            return ((p[0] - l) / w, (p[1] - t) / h) if normalize else p

        x, y = cv(self.start)
        nodes: List[Dict[str, Any]] = [{"cmd": "M", "x": x, "y": y}]
        cur = self.start
        for s in self.segs:
            x, y = cv(s.end)
            if s.cmd == "L":
                head = g.heading(cur, s.end) if hv else None
                if head == "H":
                    nodes.append({"cmd": "H", "x": x})
                elif head == "V":
                    nodes.append({"cmd": "V", "y": y})
                else:
                    nodes.append({"cmd": "L", "x": x, "y": y})
            elif s.cmd == "Q":
                cx, cy = cv(s.pts[0])
                nodes.append({"cmd": "Q", "x": x, "y": y, "cx": cx, "cy": cy})
            else:
                (c1x, c1y), (c2x, c2y) = cv(s.pts[0]), cv(s.pts[1])
                nodes.append({"cmd": "C", "x": x, "y": y,
                              "c1x": c1x, "c1y": c1y, "c2x": c2x, "c2y": c2y})
            cur = s.end
        return nodes

    def to_svg(self, digits: int = 2) -> str:
        f = lambda p: f"{round(p[0], digits):g} {round(p[1], digits):g}"   # noqa: E731
        return " ".join([f"M {f(self.start)}"] +
                        [f"{s.cmd} " + " ".join(f(p) for p in s.pts) for s in self.segs])


def _bezier(ctrl: Sequence[Pt], t: float) -> Pt:
    pts = list(ctrl)
    while len(pts) > 1:                              # de Casteljau
        pts = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
               for a, b in zip(pts, pts[1:])]
    return pts[0]


def concat(routes: Iterable[Route]) -> Route:
    routes = list(routes)
    out = routes[0]
    for r in routes[1:]:
        out = out.joined(r)
    return out
