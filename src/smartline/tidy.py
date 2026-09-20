"""Clean-up operations on a finished line - pure Python, no Qt.

    unkink(route, ctx, max_jog=40)         remove kinks: short jogs, hairpins, spurs
    nearest_route(route, others)           which other line does this one run with?
    follow(route, guide, ctx)              re-route the line as a bus member of *guide*

A *kink* is three changes of direction packed into a short distance.  The two
that are closest together are the ends of one short section, so removing a kink
means collapsing that section: one of the two runs it joins is slid sideways
until it lines up with the other, which deletes both bends at once (a hairpin
collapses onto itself and disappears entirely).  A move is only accepted if the
line ends stay put, it does not cut into a shape's clearance more than before,
and it does not land on top of another line.
"""
from __future__ import annotations

from dataclasses import replace
from typing import List, Optional, Sequence, Set, Tuple

from . import edit
from . import geometry as g
from .geometry import Pt
from .route import Route
from .routers import BusRouter, RouteContext


def _violations(pts: Sequence[Pt], ctx: RouteContext) -> int:
    hulls = [g.inflate(r, ctx.clearance - 0.01) for r in ctx.obstacles]
    return sum(1 for i in range(len(pts) - 1) for h in hulls if g.segment_hits(pts[i], pts[i + 1], h))


def _shared(pts: Sequence[Pt], others: Sequence[Sequence[Pt]], tol: float) -> float:
    return sum(g.overlap_length(pts, o, tol) for o in others) if tol > 0 else 0.0


def kinks(route: Route, max_jog: float = 40.0) -> List[Tuple[int, float]]:
    """``[(segment index, length), ...]`` of interior sections shorter than
    *max_jog*, shortest first - each one is the middle of a kink."""
    if not route.is_polyline:
        return []
    a = route.anchors()
    found = [(s, g.dist(a[s], a[s + 1])) for s in range(1, len(a) - 2)]
    return sorted([k for k in found if k[1] < max_jog], key=lambda k: k[1])


def unkink(route: Route, ctx: Optional[RouteContext] = None, max_jog: float = 40.0,
           others: Sequence[Sequence[Pt]] = ()) -> Optional[Route]:
    """Remove kinks from a straight-segment line.  Returns the cleaned Route, or
    ``None`` if there was nothing to remove (curved lines are left alone).

    *max_jog*  a section shorter than this, between two bends, is a kink.
    *ctx*      obstacles / clearance / ``wire_spacing`` to respect (optional).
    *others*   polylines of the other lines, so a run is never slid onto one.
    """
    if not route.is_polyline:
        return None
    ctx = ctx or RouteContext()
    tol = ctx.wire_spacing * 0.5
    current = edit.normalize(route)
    before = current.to_svg(4)
    dead: Set[Tuple[Pt, Pt]] = set()                  # kinks that cannot be removed legally
    for _ in range(200):
        a = current.anchors()
        todo = [(s, l) for s, l in kinks(current, max_jog) if (a[s], a[s + 1]) not in dead]
        if not todo:
            break
        s, _len = todo[0]
        flat = current.flatten()
        base_bad, base_shared, base_bends = _violations(flat, ctx), _shared(flat, others, tol), g.bends(flat)
        options = []
        for cand in _collapses(current, s):
            pts = cand.flatten()
            if g.dist(pts[0], flat[0]) > 1e-6 or g.dist(pts[-1], flat[-1]) > 1e-6:
                continue                               # the ends are pinned
            if g.bends(pts) >= base_bends:
                continue
            if _violations(pts, ctx) > base_bad or _shared(pts, others, tol) > base_shared + 1.0:
                continue
            options.append((g.bends(pts), g.length(pts), cand))
        if options:
            current = min(options, key=lambda o: o[:2])[2]
        else:
            dead.add((a[s], a[s + 1]))
    return current if current.to_svg(4) != before else None


def _collapses(route: Route, s: int) -> List[Route]:
    """Ways of making section *s* vanish."""
    a = route.anchors()
    out: List[Route] = []
    if g.heading(a[s], a[s + 1]) and g.heading(a[s - 1], a[s]) and g.heading(a[s + 1], a[s + 2]):
        # rectilinear: slide the run before it onto the run after it, or the other way round
        for seg, onto in ((s - 1, a[s + 1]), (s + 1, a[s])):
            try:
                out.append(edit.normalize(edit.move_segment(route, seg, onto, pin_ends=False)))
            except (ValueError, IndexError):
                pass
    else:
        # free angle: drop one or both of its vertices
        for drop in ((s, s + 1), (s,), (s + 1,)):
            pts = [p for k, p in enumerate(a) if k not in drop]
            if len(pts) >= 2:
                out.append(edit.normalize(Route(pts[0], Route.from_points(pts).segs, dict(route.meta))))
    return out


# --------------------------------------------------------------------- bus follow

def nearest_route(route: Route, others: Sequence[Sequence[Pt]], samples: int = 24) -> Optional[int]:
    """Index of the polyline in *others* that *route* runs closest to on average
    (the line it most plausibly belongs with), or ``None`` if there are none."""
    flat = route.flatten()
    total = g.length(flat)
    if total < 1e-9 or not others:
        return None
    pts, acc, k = [], 0.0, 0
    for n in range(samples + 1):                       # evenly spaced sample points
        target = total * n / samples
        while k < len(flat) - 2 and acc + g.dist(flat[k], flat[k + 1]) < target:
            acc += g.dist(flat[k], flat[k + 1])
            k += 1
        seg = g.dist(flat[k], flat[k + 1]) or 1.0
        t = min(1.0, max(0.0, (target - acc) / seg))
        pts.append((flat[k][0] + (flat[k + 1][0] - flat[k][0]) * t,
                    flat[k][1] + (flat[k + 1][1] - flat[k][1]) * t))
    scores = []
    for idx, o in enumerate(others):
        o = g.simplify(o)
        if len(o) >= 2:
            scores.append((sum(g.project_on_polyline(o, p)[0] for p in pts) / len(pts), idx))
    return min(scores)[1] if scores else None


def follow(route: Route, guide: Sequence[Pt], ctx: RouteContext,
           others: Sequence[Sequence[Pt]] = (), tidy: bool = True,
           max_jog: float = 40.0) -> Route:
    """Re-route *route* between its own end points as a bus member of *guide*:
    parallel to it at ``ctx.bus_pitch``, on the side the line already favours,
    going around shapes, then un-kinked."""
    guide = g.simplify(guide)
    flat = route.flatten()
    start, end = flat[0], flat[-1]
    # side: where most of the line is now (its ends may sit on either side of the guide)
    votes = sum(g.side_of_polyline(guide, p) for p in flat)
    want = 1.0 if votes >= 0 else -1.0
    flip = g.side_of_polyline(guide, start) != want
    tol = ctx.wire_spacing * 0.5 or ctx.bus_pitch * 0.4
    router, new = BusRouter(), None
    for lane in range(1, 9):                           # first lane not already taken by another line
        c = replace(ctx, guide=guide, guides=(), flip=flip, bus_capture=0.0,
                    bus_pitch=ctx.bus_pitch * lane, auto_posture=None, prev_heading=None)
        cand = router.shape(start, end, c)
        taken = _shared(cand.flatten(), others, tol)
        if new is None or taken < best_taken:
            new, best_taken = cand, taken
        if taken <= ctx.bus_pitch:
            break
    new.meta.update(router="bus", flip=flip, posture=None)
    new.meta.pop("legs", None)
    if tidy:
        new = unkink(new, ctx, max_jog, others=others) or new
    return new
