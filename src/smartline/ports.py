"""Port exits - pure Python.

A line that is attached to a shape must leave it squarely: at right angles to
the side it is attached to, and straight for at least the clearance distance
before its first bend.  That little straight piece is the *stub*.

Rather than teaching every router about ports, routing is done *between the stub
ends*: the stub end lies exactly on the shape's clearance hull, so the router
treats the line's own shape as the obstacle it is (normally a hull that contains
an end point has to be ignored), and the stub is then put back on.

    Exit = (stub_end, outward_normal, shape_rect)
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, List, Optional, Sequence, Tuple

from . import geometry as g
from .geometry import Pt, Rect
from .route import Route
from .routers import RouteContext, Router

Exit = Tuple[Pt, Pt, Rect]
ExitFinder = Callable[[Pt, float], Optional[Exit]]


def rect_exit_finder(rects: Sequence[Rect], tol: float = 3.0) -> ExitFinder:
    """Default finder: a point on the perimeter of one of *rects* is a port."""
    rects = [tuple(r) for r in rects]
    return lambda p, clearance: g.port_exit(p, rects, clearance, tol)


def shape_with_exits(router: Router, start: Pt, end: Pt, ctx: RouteContext,
                     exit_a: Optional[Exit] = None, exit_b: Optional[Exit] = None) -> Route:
    """``router.shape(start, end)`` with square stubs at the attached ends."""
    if exit_a is None and exit_b is None:
        return Route.coerce(router.shape(start, end, ctx))
    s2 = exit_a[0] if exit_a else start
    e2 = exit_b[0] if exit_b else end
    posture = ctx.auto_posture
    if exit_a:
        n = exit_a[1]
        ahead = (e2[0] - s2[0]) * n[0] + (e2[1] - s2[1]) * n[1] >= 0
        along_x = abs(n[0]) > 0.5
        # carry straight on out of the port if the target lies ahead, else turn at the stub end
        posture = ("HV" if along_x else "VH") if ahead else ("VH" if along_x else "HV")
    c = replace(ctx, auto_posture=posture, prev_heading=None,
                start_normal=exit_a[1] if exit_a else None,
                end_normal=exit_b[1] if exit_b else None)
    out = Route(start)
    if exit_a:
        out.line_to(s2)
    if g.dist(s2, e2) > 1e-6:
        piece = Route.coerce(router.shape(s2, e2, c))
        if g.dist(piece.end, e2) > 1e-6:                  # e.g. an angle-snapping router
            piece = piece.joined(Route.from_points([piece.end, e2]))
        out = out.joined(piece) if out.segs else piece
        out.meta = dict(piece.meta)
    if exit_b:
        out.line_to(end)
    if out.start != start:
        out = Route(start, out.segs, out.meta)
    out.meta.pop("legs", None)
    return out


def obstacle_hits(flat: Sequence[Pt], rects: Sequence[Rect],
                  exit_a: Optional[Exit] = None, exit_b: Optional[Exit] = None) -> int:
    """Number of (segment, shape) intersections - the quantity a re-route must
    drive to zero before anything else.  A stub does not count against the shape
    it is attached to."""
    last = len(flat) - 2
    total = 0
    for k in range(len(flat) - 1):
        for r in rects:
            r = tuple(r)
            if (k == 0 and exit_a and r == tuple(exit_a[2])) or (k == last and exit_b and r == tuple(exit_b[2])):
                continue
            if g.segment_hits(flat[k], flat[k + 1], r):
                total += 1
    return total


def stubs_ok(flat: Sequence[Pt], clearance: float,
             exit_a: Optional[Exit] = None, exit_b: Optional[Exit] = None) -> bool:
    """Do the attached ends still leave squarely and run straight for *clearance*?"""
    def ok(p, q, n):
        v = (q[0] - p[0], q[1] - p[1])
        along = v[0] * n[0] + v[1] * n[1]
        across = abs(v[0] * n[1] - v[1] * n[0])
        return along >= clearance - 0.5 and across < 0.5
    if len(flat) < 2:
        return False
    if exit_a and not ok(flat[0], flat[1], exit_a[1]):
        return False
    if exit_b and not ok(flat[-1], flat[-2], exit_b[1]):
        return False
    return True
