"""Repair finished lines that a shape now overlaps - pure Python, no Qt.

    new = repair(route, shape_rect, ctx)          # None if the line was already clear

Only the part of the line that runs through the shape is replaced.  The line is
cut where it enters and leaves the shape's clearance hull, the gap is re-routed with **the routing method that part of the line was drawn
with** (see :meth:`Route.legs`), and the result is spliced back in - so the rest
of the line, including any manual edits, is left exactly as it was.

A non-avoiding method (``straight``, ``ortho``, ``octilinear``, ``cubic`` ...) is
used through :meth:`Router.avoiding`: same style, plus a walkaround pass.

Lines also avoid *each other*: a repaired piece may cross another line but may
not share track with it.  If it would, it is routed again one lane further out.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, List, Optional, Sequence, Tuple

from . import edit
from . import geometry as g
from .geometry import Pt, Rect
from .route import Route, _bezier
from .ports import obstacle_intrusion, rect_exit_finder, shape_with_exits
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


def _cut(route: Route, first: int, last: int, hull: Rect) -> Tuple[Route, int, int]:
    """Give *route* vertices exactly where it enters and leaves *hull* around the
    offending run of segments ``first .. last-1``; returns ``(route, i, j)``.

    Cutting at the hull - instead of re-using whatever vertices happen to exist -
    keeps a repair local (a two-point line is not re-planned end to end) and gives
    every lane its own entry and exit, so detours of neighbouring lines nest
    instead of piling onto the same track.
    """
    a = route.anchors()
    n = len(a) - 1

    def clip(s):
        return g.clip_segment(a[s], a[s + 1], hull) if route.segs[s].cmd == "L" else None

    exit_at = None                                     # (segment, t) or a vertex index
    s = last - 1
    while True:
        c = clip(s)
        if c is not None and c[1] < 1 - 1e-9:
            exit_at = (s, c[1])
            break
        if s + 1 >= n or not g.contains(hull, a[s + 1], strict=True):
            break
        s += 1
    j = s + 1
    entry_at = None
    s = first
    while True:
        c = clip(s)
        if c is not None and c[0] > 1e-9:
            entry_at = (s, c[0])
            break
        if s == 0 or not g.contains(hull, a[s], strict=True):
            break
        s -= 1
    i = s
    if exit_at is not None:                            # later cut first: indices before it stay valid
        route = edit.insert_anchor(route, exit_at[0], exit_at[1])
        j = exit_at[0] + 1
    if entry_at is not None:
        route = edit.insert_anchor(route, entry_at[0], entry_at[1])
        i = entry_at[0] + 1
        j += 1
    return route, i, j


def _default_lookup(name: Optional[str]) -> Optional[Router]:
    from . import registry
    try:
        return registry.create(name) if name else None
    except KeyError:
        return None


def _note_crossings(route: Route, others: Sequence[Sequence[Pt]]) -> None:
    n = sum(g.crossings(route.flatten(), o) for o in others)
    route.meta.pop("crossings", None)
    if n:
        route.meta["crossings"] = n


def _search(router: Router, leg: dict, ctx: RouteContext, others: Sequence[Sequence[Pt]],
            ends: Callable, find_exit, old_flat: Optional[Sequence[Pt]] = None, max_lanes: int = 8):
    """Try the routing method in every legal variation and keep the best piece.

    Priorities, strictly in this order:
      1. do not pass through a shape                  (obstacle hits)
      2. do not collide with another line: never run on top of one (shared track),
         then cross as few as possible             (crossings)
      3. (shared track is cured by taking the next lane out)
      4. go the natural (shorter) way round, in the innermost lane, with the posture it was drawn with
      5. be short
    Variations: lane 0..max_lanes (hulls pushed out by ``wire_spacing`` each), posture
    flip, and which way round a shape the walkaround goes (auto / cw / ccw).

    ``ends(extra)`` -> ``(p, q, at_start, at_end, payload)`` gives the piece's end points for
    a lane whose hulls are inflated by *extra* (and whether they are the line's own ends).
    Returns ``(score, piece, payload, flip, shared_track)``.
    """
    spacing = ctx.wire_spacing if others else 0.0
    tol = spacing * 0.5
    rects = [tuple(o) for o in ctx.obstacles]
    drawn_flip = bool(leg.get("flip"))
    best = None
    for lane in range(max_lanes + 1 if spacing > 0 else 1):
        extra = lane * spacing
        margins = {r: extra for r in rects} if lane else {}
        p, q, at_start, at_end, payload = ends(extra)
        exit_a = find_exit(p, ctx.clearance + extra) if (find_exit and at_start) else None
        exit_b = find_exit(q, ctx.clearance + extra) if (find_exit and at_end) else None
        lane_ok = False
        for flip in (drawn_flip, not drawn_flip):
            for rank, winding in enumerate(("auto", "cw", "ccw")):
                c = replace(ctx, flip=flip, auto_posture=leg.get("posture"), prev_heading=None,
                            margins=margins, winding=winding, avoid_lines=others)
                piece = shape_with_exits(router, p, q, c, exit_a, exit_b)
                if router.free_angle and piece.is_polyline and not (exit_a or exit_b):
                    piece = Route.from_points(g.shortcut(piece.flatten(), c.hulls(p, q)))
                flat = piece.flatten()
                hits_n = round(obstacle_intrusion(flat, rects, exit_a, exit_b), 3)
                cross_n = sum(g.crossings(flat, o) for o in others)
                if spacing > 0:
                    along = (_new_shared_track(flat, others, tol, old_flat) if old_flat is not None
                             else sum(g.overlap_length(flat, o, tol) for o in others))
                else:
                    along = 0.0
                crowded = along > max(spacing, 4.0)
                score = (hits_n, crowded, cross_n, rank > 0, lane, flip != drawn_flip, g.length(flat))
                if best is None or score < best[0]:
                    best = (score, piece, payload, flip, along if crowded else 0.0)
                lane_ok = lane_ok or (rank == 0 and not crowded)
                if hits_n == 0 and cross_n == 0 and not crowded and rank == 0:
                    return best
        if lane_ok:
            break                                  # a further lane only cures shared track
    return best


def repair(route: Route, rect: Rect, ctx: RouteContext,
           lookup: RouterLookup = _default_lookup, max_passes: int = 4,
           others: Sequence[Sequence[Pt]] = (), max_lanes: int = 8,
           find_exit="rects") -> Optional[Route]:
    """Re-route the parts of *route* that pass through *rect*.

    *ctx* supplies the current obstacles (which should include *rect*'s shape),
    the clearance and ``wire_spacing``.  *others* are the polylines of the other
    lines in the drawing: when ``ctx.wire_spacing > 0`` the new piece may cross
    them but must not run *along* one.  If it would, the piece is routed again in
    the next "lane" - the same routing method, with the hulls it goes around
    pushed out by one more ``wire_spacing`` - until it has a track of its own.

    Returns the repaired Route, or ``None`` when the line does not touch the
    shape.  What could not be achieved is flagged in ``route.meta``:
    ``"unresolved"`` (still crosses the shape - typically a line end sits inside
    it) and ``"overlaps"`` (still shares track with another line).
    """
    if not hits(route, rect):
        return None
    rect = tuple(rect)
    if find_exit == "rects":
        find_exit = rect_exit_finder(ctx.obstacles)
    current = route
    shared = 0.0
    for _ in range(max_passes):
        bad = hit_segments(current, rect)
        if not bad:
            break
        first, last = bad[0], bad[0] + 1
        while last in bad:                             # "next segment" == "end vertex" index
            last += 1
        leg = leg_at(current, first + 1)
        router = (lookup(leg.get("router")) or HugRouter(OrthoRouter())).avoiding()
        curved = any(seg.cmd != "L" for seg in current.segs[first:last])

        def ends(extra, first=first, last=last, current=current, curved=curved):
            # lane 0 is the plain repair; further lanes push every hull out so that the
            # piece clears lines already hugging the shape *and* its neighbours
            if curved:
                # curve vertices come from smoothing, not from the user: redo the whole leg
                base, (i, j) = current, leg_span(current, first + 1)
            else:
                base, i, j = _cut(current, first, last, g.inflate(rect, ctx.clearance + extra))
            pts = base.anchors()
            return pts[i], pts[j], i == 0, j == len(pts) - 1, (base, i, j)

        _score, piece, (base, i, j), _flip, along = _search(router, leg, ctx, others, ends, find_exit,
                                                            max_lanes=max_lanes)
        a = base.anchors()
        shared = max(shared, along)
        if g.dist(piece.start, a[i]) > 1e-6 or g.dist(piece.end, a[j]) > 1e-6:
            piece = Route.from_points([a[i]] + piece.flatten() + [a[j]])
        spliced = Route(base.start, list(base.segs[:i]) + list(piece.segs) + list(base.segs[j:]),
                        dict(current.meta))
        if spliced.to_svg(4) == current.to_svg(4):
            break                                      # no progress: give up rather than loop
        current = spliced
    current = edit.normalize(current)
    for key in ("unresolved", "overlaps"):
        current.meta.pop(key, None)
    if hits(current, rect):
        current.meta["unresolved"] = True
    if shared > 0:
        current.meta["overlaps"] = round(shared, 1)
    _note_crossings(current, others)
    return current


# ------------------------------------------------------ re-route with current settings

def near(route: Route, rect: Rect, distance: float) -> bool:
    """Does *route* pass within *distance* of *rect* (i.e. is it, or should it
    be, part of the traffic around that shape)?"""
    return hits(route, g.inflate(rect, distance))


def _new_shared_track(flat: Sequence[Pt], others: Sequence[Sequence[Pt]], tol: float,
                      old: Sequence[Pt]) -> float:
    """Track shared with *others*, ignoring the run-in and run-out: the first
    and last sections start at pinned points, so where they stay on the line's
    previous path their closeness to a neighbour is nothing a new lane can cure -
    counting it would push every line to the outermost lane."""
    total = 0.0
    last = len(flat) - 2
    for k in range(len(flat) - 1):
        seg = [flat[k], flat[k + 1]]
        if k in (0, last) and g.overlap_length(seg, old, 0.5) >= g.dist(*seg) - 1e-6:
            continue
        total += sum(g.overlap_length(seg, o, tol) for o in others)
    return total


def refresh(route: Route, ctx: RouteContext, lookup: RouterLookup = _default_lookup,
            others: Sequence[Sequence[Pt]] = (), max_lanes: int = 8, find_exit="rects") -> Route:
    """Route the whole line again, leg by leg, with the settings in *ctx*.

    :func:`repair` only touches lines that a shape overlaps, so a line that
    already goes around a shape keeps the clearance, spacing and bend penalty it
    was routed with.  ``refresh`` is the "apply my new settings" operation: every
    drawn leg is re-run between its own end points with the method, posture and
    flip it was drawn with, avoiding the shapes and keeping off *others*.
    Manual edits inside a leg are replaced; the leg end points are kept.
    """
    if find_exit == "rects":
        find_exit = rect_exit_finder(ctx.obstacles)
    a = route.anchors()
    old_flat = route.flatten()
    pieces: List[Route] = []
    first = 0
    while first < len(a) - 1:
        lo, hi = leg_span(route, first + 1)
        hi = max(hi, first + 1)
        leg = leg_at(route, first + 1)
        router = (lookup(leg.get("router")) or HugRouter(OrthoRouter())).avoiding()
        at_a, at_b = first == 0, hi == len(a) - 1

        def ends(extra, p=a[first], q=a[hi], at_a=at_a, at_b=at_b):
            return p, q, at_a, at_b, None

        best = _search(router, leg, ctx, others, ends, find_exit, old_flat=old_flat, max_lanes=max_lanes)
        piece = best[1]
        if g.dist(piece.end, a[hi]) > 1e-6:
            piece = Route.from_points(piece.flatten() + [a[hi]])
        piece.meta = {"router": leg.get("router"), "flip": best[3], "posture": leg.get("posture")}
        pieces.append(piece)
        first = hi
    out = pieces[0]
    for p in pieces[1:]:
        out = out.joined(p)
    out = edit.normalize(out)
    keep = {k: v for k, v in route.meta.items() if k not in ("legs", "unresolved", "overlaps", "crossings")}
    out.meta = {**keep, **{k: v for k, v in out.meta.items() if k in ("legs", "router", "flip", "posture")}}
    _note_crossings(out, others)
    return out
