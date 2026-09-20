"""Editing operations on a finished Route - pure Python, no Qt.

Drawing and editing are different jobs, so they are different classes, but they
share one data model: a router *produces* a :class:`~smartline.route.Route`,
these functions *transform* one.  Every function returns a new Route and leaves
its input untouched, which makes undo trivial (keep the old one) and lets the
interactive editor apply each drag to the route as it was when the drag began.

Handles - what a user can grab:

    anchor    an on-curve vertex                      (index 0 .. n)
    segment   the middle of a straight segment        (drag the whole section)
    c1 / c2   the two control points of a cubic       (the tangent handles)
    q         the control point of a quadratic

Orthogonal routes stay orthogonal: dragging a vertex slides its neighbours along
their axes, dragging a section moves it perpendicular to itself, and a section
that touches a pinned end (a port) grows a jog instead of dragging the end off
its port.  Cubic control points are *linked* across an anchor so the curve stays
smooth: "aligned" keeps the two tangent handles collinear, "mirrored" also makes
them the same length, "free" breaks the tangent (a cusp).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from . import geometry as g
from .geometry import Pt
from .route import Route, Seg, _bezier


@dataclass(frozen=True)
class Handle:
    kind: str          # "anchor" | "segment" | "c1" | "c2" | "q"
    index: int         # anchor index for "anchor", segment index otherwise
    pos: Pt
    anchor: Optional[Pt] = None     # for control handles: the anchor its tangent line runs to

    @property
    def is_control(self) -> bool:
        return self.kind in ("c1", "c2", "q")


# ------------------------------------------------------------------ inspection

def is_orthogonal(route: Route) -> bool:
    """All segments are straight and axis-aligned (zero-length ones are ignored)."""
    if not route.is_polyline or not route.segs:
        return False
    a = route.anchors()
    return all(g.heading(a[i], a[i + 1]) is not None or g.dist(a[i], a[i + 1]) < 1e-6
               for i in range(len(a) - 1))


def handles(route: Route, segments: bool = True) -> List[Handle]:
    """Everything grabbable, controls first so they win hit-tests over anchors
    they sit close to."""
    a = route.anchors()
    out: List[Handle] = []
    for s, seg in enumerate(route.segs):
        if seg.cmd == "C":
            out.append(Handle("c1", s, seg.pts[0], a[s]))
            out.append(Handle("c2", s, seg.pts[1], a[s + 1]))
        elif seg.cmd == "Q":
            out.append(Handle("q", s, seg.pts[0], a[s]))
    out.extend(Handle("anchor", i, p) for i, p in enumerate(a))
    if segments:
        for s, seg in enumerate(route.segs):
            if seg.cmd == "L" and g.dist(a[s], a[s + 1]) > 1e-6:
                out.append(Handle("segment", s, ((a[s][0] + a[s + 1][0]) / 2, (a[s][1] + a[s + 1][1]) / 2)))
    return out


def nearest_segment(route: Route, p: Pt, samples: int = 32) -> Tuple[int, float, float]:
    """``(segment index, t, distance)`` of the point on the route closest to *p*."""
    a = route.anchors()
    best = (0, 0.0, float("inf"))
    for s, seg in enumerate(route.segs):
        if seg.cmd == "L":
            d, _, t, _ = g.project_on_polyline([a[s], a[s + 1]], p)
        else:
            ctrl = (a[s],) + seg.pts
            poly = [_bezier(ctrl, k / samples) for k in range(samples + 1)]
            d, i, u, _ = g.project_on_polyline(poly, p)
            t = (i + u) / samples
        if d < best[2]:
            best = (s, t, d)
    return best


# ------------------------------------------------------------------- internals

def _with(route: Route, start: Pt, segs: Sequence[Seg]) -> Route:
    return Route(start, list(segs), dict(route.meta))


def _retarget(seg: Seg, end: Pt) -> Seg:
    return Seg(seg.cmd, seg.pts[:-1] + (end,))


def _shift(p: Pt, d: Pt) -> Pt:
    return (p[0] + d[0], p[1] + d[1])


def _from_anchors(route: Route, anchors: Sequence[Pt]) -> Route:
    return _with(route, anchors[0], [Seg("L", (p,)) for p in anchors[1:]])


# ------------------------------------------------------------------ operations

def move_anchor(route: Route, i: int, p: Pt, keep_orthogonal: bool = True) -> Route:
    """Move vertex *i* to *p*."""
    a = route.anchors()
    n = len(a) - 1
    if keep_orthogonal and is_orthogonal(route):
        if n == 1:                                   # a single run: grow an elbow
            fixed = a[1 - i]
            h = g.heading(a[0], a[1]) or "H"
            # leave the fixed end along the original run, arrive at p perpendicular to it
            elbow = (p[0], fixed[1]) if h == "H" else (fixed[0], p[1])
            pts = [p, elbow, fixed] if i == 0 else [fixed, elbow, p]
            return _from_anchors(route, pts)
        a = list(a)
        heads = [g.heading(a[k], a[k + 1]) for k in range(n)]
        for j, s in ((i - 1, i - 1), (i + 1, i)):    # neighbour j via segment s
            if 0 <= j <= n and 0 < i < n and j in (0, n) and heads[s]:
                # neighbour is a pinned end: this vertex may only slide along the run
                p = (p[0], a[j][1]) if heads[s] == "H" else (a[j][0], p[1])
        a[i] = p
        for j, s in ((i - 1, i - 1), (i + 1, i)):
            if 0 <= j <= n and heads[s]:
                a[j] = (a[j][0], p[1]) if heads[s] == "H" else (p[0], a[j][1])
        return _from_anchors(route, a)

    d = (p[0] - a[i][0], p[1] - a[i][1])
    segs = list(route.segs)
    start = route.start
    if i == 0:
        start = p
    else:
        s = segs[i - 1]                              # segment arriving at the anchor
        pts = list(s.pts)
        pts[-1] = p
        if s.cmd == "C":
            pts[1] = _shift(pts[1], d)               # its tangent handle travels with it
        segs[i - 1] = Seg(s.cmd, tuple(pts))
    if i < len(segs) and segs[i].cmd == "C":         # segment leaving the anchor
        s = segs[i]
        segs[i] = Seg("C", (_shift(s.pts[0], d),) + s.pts[1:])
    return _with(route, start, segs)


def move_segment(route: Route, s: int, p: Pt, grab: Optional[Pt] = None,
                 keep_orthogonal: bool = True, pin_ends: bool = True) -> Route:
    """Drag straight section *s* so that it passes through *p*.

    Axis-aligned sections move perpendicular to themselves.  If the section
    touches a route end and ``pin_ends`` is set, the end stays put and a jog is
    inserted.  Other sections translate by ``p - grab``.
    """
    if route.segs[s].cmd != "L":
        raise ValueError("only straight sections can be dragged; use the control handles on curves")
    a = list(route.anchors())
    n = len(a) - 1
    h = g.heading(a[s], a[s + 1])
    if h and keep_orthogonal:
        na = (a[s][0], p[1]) if h == "H" else (p[0], a[s][1])
        nb = (a[s + 1][0], p[1]) if h == "H" else (p[0], a[s + 1][1])
    else:
        grab = grab or ((a[s][0] + a[s + 1][0]) / 2, (a[s][1] + a[s + 1][1]) / 2)
        d = (p[0] - grab[0], p[1] - grab[1])
        na, nb = _shift(a[s], d), _shift(a[s + 1], d)
    if not route.is_polyline:                        # neighbours may be curves: just move both ends
        r = move_anchor(route, s, na, keep_orthogonal=False)
        return move_anchor(r, s + 1, nb, keep_orthogonal=False)
    head = [a[0]] if (s == 0 and pin_ends) else []
    tail = [a[n]] if (s + 1 == n and pin_ends) else []
    return _from_anchors(route, a[:s] + head + [na, nb] + tail + a[s + 2:])


def move_control(route: Route, s: int, which: str, p: Pt, link: str = "aligned") -> Route:
    """Move a control point; ``link`` decides what its partner across the anchor does.

    ``"aligned"``  partner stays collinear (smooth join), keeps its own length
    ``"mirrored"`` partner collinear *and* the same length (symmetric join)
    ``"free"``     partner untouched (cusp)
    """
    segs = list(route.segs)
    seg = segs[s]
    if which == "q":
        segs[s] = Seg("Q", (p, seg.pts[1]))
        return _with(route, route.start, segs)
    k = 0 if which == "c1" else 1
    pts = list(seg.pts)
    pts[k] = p
    segs[s] = Seg("C", tuple(pts))
    if link != "free":
        a = route.anchors()
        t, anchor, tk = (s - 1, a[s], 1) if which == "c1" else (s + 1, a[s + 1], 0)
        if 0 <= t < len(segs) and segs[t].cmd == "C":
            v = (p[0] - anchor[0], p[1] - anchor[1])
            vl = math.hypot(*v)
            if vl > 1e-9:
                partner = segs[t].pts[tk]
                length = vl if link == "mirrored" else g.dist(partner, anchor)
                q = (anchor[0] - v[0] / vl * length, anchor[1] - v[1] / vl * length)
                tp = list(segs[t].pts)
                tp[tk] = q
                segs[t] = Seg("C", tuple(tp))
    return _with(route, route.start, segs)


def insert_anchor(route: Route, s: int, t: float) -> Route:
    """Split segment *s* at parameter *t* without changing the route's shape."""
    t = min(max(t, 0.0), 1.0)
    a = route.anchors()
    seg = route.segs[s]
    ctrl = [a[s]] + list(seg.pts)
    left, right = [ctrl[0]], [ctrl[-1]]
    while len(ctrl) > 1:                              # de Casteljau subdivision
        ctrl = [(u[0] + (v[0] - u[0]) * t, u[1] + (v[1] - u[1]) * t) for u, v in zip(ctrl, ctrl[1:])]
        left.append(ctrl[0])
        right.append(ctrl[-1])
    right.reverse()
    segs = list(route.segs)
    segs[s:s + 1] = [Seg(seg.cmd, tuple(left[1:])), Seg(seg.cmd, tuple(right[1:]))]
    return _with(route, route.start, segs)


def delete_anchor(route: Route, i: int) -> Route:
    """Remove interior vertex *i*, joining its two segments into one."""
    n = len(route.segs)
    if not 0 < i < n:
        raise ValueError("only interior anchors can be deleted")
    a = route.anchors()
    s1, s2 = route.segs[i - 1], route.segs[i]
    if s1.cmd == "L" and s2.cmd == "L":
        merged = Seg("L", (s2.end,))
    else:
        c1 = s1.pts[0] if s1.cmd != "L" else a[i - 1]
        c2 = s2.pts[-2] if s2.cmd != "L" else s2.end
        merged = Seg("C", (c1, c2, s2.end))
    segs = list(route.segs)
    segs[i - 1:i + 1] = [merged]
    return _with(route, route.start, segs)


def end_span(route: Route, which: str = "end") -> List[int]:
    """Segment indices that :func:`trim_end` would remove from the ``"start"`` or
    ``"end"`` of the line: the terminal section, plus the curve that joins it to
    the rest of the line when there is one (a corner blend belongs to the section
    it rounds off - leaving it behind would end the line in a stray hook)."""
    n = len(route.segs)
    if n == 0:
        return []
    order = list(range(n)) if which == "start" else list(range(n - 1, -1, -1))
    span = [order[0]]
    if route.segs[order[0]].cmd == "L" and n > 1 and route.segs[order[1]].cmd != "L":
        span.append(order[1])
    return sorted(span)


def trim_end(route: Route, which: str = "end") -> Optional[Route]:
    """Delete the last section at the ``"start"`` or ``"end"`` of the line (see
    :func:`end_span`).  Returns the shortened Route, or ``None`` when nothing
    would be left - the caller should then delete the line itself."""
    span = end_span(route, which)
    if not span or len(span) >= len(route.segs):
        return None
    a = route.anchors()
    if which == "start":
        keep = route.segs[span[-1] + 1:]
        out = _with(route, a[span[-1] + 1], keep)
    else:
        out = _with(route, route.start, route.segs[:span[0]])
    legs = out.meta.get("legs")
    if legs:                                           # the surviving last leg now stops at the new end
        legs = [dict(l) for l in legs]
        legs[-1]["end"] = out.end
        out.meta["legs"] = legs
    return out


def span_points(route: Route, span: Sequence[int]) -> List[Pt]:
    """Flattened geometry of a run of segments (for highlighting)."""
    if not span:
        return []
    a = route.anchors()
    piece = Route(a[span[0]], [route.segs[k] for k in span])
    return piece.flatten()


def convert_segment(route: Route, s: int, cmd: str) -> Route:
    """Turn segment *s* into a line (``"L"``) or a cubic (``"C"``)."""
    a = route.anchors()
    seg = route.segs[s]
    p0, p3 = a[s], a[s + 1]
    if cmd == seg.cmd:
        return route
    if cmd == "L":
        new = Seg("L", (p3,))
    elif cmd == "C":
        if seg.cmd == "Q":                            # exact degree elevation
            q = seg.pts[0]
            c1 = (p0[0] + 2 / 3 * (q[0] - p0[0]), p0[1] + 2 / 3 * (q[1] - p0[1]))
            c2 = (p3[0] + 2 / 3 * (q[0] - p3[0]), p3[1] + 2 / 3 * (q[1] - p3[1]))
        else:
            c1 = (p0[0] + (p3[0] - p0[0]) / 3, p0[1] + (p3[1] - p0[1]) / 3)
            c2 = (p0[0] + (p3[0] - p0[0]) * 2 / 3, p0[1] + (p3[1] - p0[1]) * 2 / 3)
        new = Seg("C", (c1, c2, p3))
    else:
        raise ValueError(cmd)
    segs = list(route.segs)
    segs[s] = new
    return _with(route, route.start, segs)


def normalize(route: Route) -> Route:
    """Tidy up after a drag: merge collinear runs, drop zero-length sections."""
    if route.is_polyline:
        pts = g.simplify(route.anchors())
        if len(pts) < 2:
            pts = [route.start, route.end]
        return _from_anchors(route, pts)
    a = route.anchors()
    segs = [seg for k, seg in enumerate(route.segs)
            if not (seg.cmd == "L" and g.dist(a[k], a[k + 1]) < 1e-6)]
    return _with(route, route.start, segs or list(route.segs))
