"""Repair finished lines that a shape now overlaps - pure Python, no Qt.

    new = repair(route, shape_rect, ctx)          # None if the line was already clear

Only the part of the line that runs through the shape is replaced.  The span is
widened to the nearest vertices that lie outside the shape's clearance hull, the
gap is re-routed with **the routing method that part of the line was drawn
with** (see :meth:`Route.legs`), and the result is spliced back in - so the rest
of the line, including any manual edits, is left exactly as it was.

A non-avoiding method (``straight``, ``ortho``, ``octilinear``, ``cubic`` ...) is
used through :meth:`Router.avoiding`: same style, plus a walkaround pass.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, List, Optional, Sequence, Tuple

from . import edit
from . import geometry as g
from .geometry import Pt, Rect
from .route import Route, _bezier
from .routers import HugRouter, OrthoRouter, RouteContext, Router

RouterLookup = Callable[[Optional[str]], Optional[Router]]


def _seg_polyline(route: Route, s: int, anchors: Sequence[Pt]) -> List[Pt]:
    seg = route.segs[s]
    if seg.cmd == "L":
        return [anchors[s], anchors[s + 1]]
    ctrl = (anchors[s],) + seg.pts
    return [_bezier(ctrl, k / 24) for k in range(25)]


def hit_segments(route: Route, rect: Rect) -> List[int]:
    """Indices of the segments that pass through the interior of *rect*."""
    a = route.anchors()
    out = []
    for s in range(len(route.segs)):
        poly = _seg_polyline(route, s, a)
        if any(g.segment_hits(poly[k], poly[k + 1], rect) for k in range(len(poly) - 1)):
            out.append(s)
    return out


def hits(route: Route, rect: Rect) -> bool:
    return bool(hit_segments(route, rect))


def leg_at(route: Route, anchor_index: int) -> dict:
    """Provenance record of the drawn leg that contains vertex *anchor_index*.

    Leg boundaries are matched by position; if an edit removed a boundary the
    lookup simply stays on the earlier leg.
    """
    legs = route.legs()
    a = route.anchors()
    k = 0
    for idx in range(1, anchor_index):               # boundaries passed before reaching the vertex
        if k < len(legs) - 1 and g.dist(a[idx], legs[k]["end"]) < 1e-6:
            k += 1
    return legs[k]


def leg_span(route: Route, anchor_index: int) -> Tuple[int, int]:
    """``(first, last)`` vertex indices of the drawn leg containing *anchor_index*."""
    legs = route.legs()
    a = route.anchors()
    k, first = 0, 0
    for idx in range(1, len(a)):
        if k < len(legs) - 1 and g.dist(a[idx], legs[k]["end"]) < 1e-6:
            if idx >= anchor_index:
                return first, idx
            k, first = k + 1, idx
    return first, len(a) - 1


def _default_lookup(name: Optional[str]) -> Optional[Router]:
    from . import registry
    try:
        return registry.create(name) if name else None
    except KeyError:
        return None


def repair(route: Route, rect: Rect, ctx: RouteContext,
           lookup: RouterLookup = _default_lookup, max_passes: int = 4) -> Optional[Route]:
    """Re-route the parts of *route* that pass through *rect*.

    *ctx* supplies the current obstacles (which should include *rect*'s shape)
    and the clearance.  Returns the repaired Route, or ``None`` when the line
    does not touch the shape.  If a crossing cannot be resolved - typically
    because a line end sits inside the shape - the best attempt is returned and
    ``route.meta["unresolved"]`` is set.
    """
    if not hits(route, rect):
        return None
    hull = g.inflate(rect, ctx.clearance)
    current = route
    for _ in range(max_passes):
        bad = hit_segments(current, rect)
        if not bad:
            break
        a = current.anchors()
        n = len(a) - 1
        # first contiguous run of offending segments, widened to vertices outside the hull
        i, j = bad[0], bad[0] + 1
        while j in bad:                                # j is both "next segment" and "end vertex"
            j += 1
        while i > 0 and g.contains(hull, a[i], strict=True):
            i -= 1
        while j < n and g.contains(hull, a[j], strict=True):
            j += 1
        leg = leg_at(current, i + 1)
        router = (lookup(leg.get("router")) or HugRouter(OrthoRouter())).avoiding()
        if any(seg.cmd != "L" for seg in current.segs[i:j]):
            # The vertices of a curve come from smoothing, not from the user, so a
            # local splice would leave kinks: redo the whole drawn leg instead.
            lo, hi = leg_span(current, i + 1)
            i, j = min(i, lo), max(j, hi)
        best: Optional[Tuple[Tuple[int, float], Route]] = None
        for flip in (bool(leg.get("flip")), not leg.get("flip")):
            c = replace(ctx, flip=flip, auto_posture=leg.get("posture"), prev_heading=None)
            piece = Route.coerce(router.shape(a[i], a[j], c))
            flat = piece.flatten()
            blocked = any(g.segment_hits(flat[k], flat[k + 1], rect) for k in range(len(flat) - 1))
            score = (1 if blocked else 0, g.length(flat))
            if best is None or score < best[0]:
                best = (score, piece)
            if not blocked:
                break                                  # the posture it was drawn with wins when it works
        piece = best[1]
        if g.dist(piece.start, a[i]) > 1e-6 or g.dist(piece.end, a[j]) > 1e-6:
            piece = Route.from_points([a[i]] + piece.flatten() + [a[j]])
        spliced = Route(current.start, list(current.segs[:i]) + list(piece.segs) + list(current.segs[j:]),
                        dict(current.meta))
        if spliced.to_svg(4) == current.to_svg(4):
            break                                      # no progress: give up rather than loop
        current = spliced
    current = edit.normalize(current)
    current.meta.pop("unresolved", None)
    if hits(current, rect):
        current.meta["unresolved"] = True
    return current
