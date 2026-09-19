"""Routing strategies for interactive ("rubber-band") line drawing.

Every router answers one question:  *given the anchor, the cursor and the
current context, what polyline should the preview show?*

    route(start, end, ctx) -> [start, ..., end]

Routers are pure Python and stateless; all interaction state (posture flip,
heading of the last committed segment, obstacles, clearance ...) arrives in a
:class:`RouteContext`.  That keeps them trivially testable and lets the same
strategies be reused for re-routing existing connectors when shapes move.

Lineage of the techniques (see README.md for full references):

* ``StraightRouter``  - classic rubber-band line (Sutherland's Sketchpad, 1963)
                        with optional polar angle snapping.
* ``OrthoRouter``     - two-segment Manhattan "L" with a *posture* toggle, as
                        in KiCad / Altium / OrCAD wire tools.
* ``OctilinearRouter``- 45 degree "metro" posture used by PCB trace tools.
* ``HugRouter``       - *walkaround*: keep the base route, but where it would
                        pierce an obstacle, slide it around that obstacle's
                        inflated hull (KiCad PNS ``WALKAROUND``; Bug-2 style
                        boundary following from robotics).
* ``BusRouter``       - follow an existing net at a fixed pitch: mitered
                        parallel (offset) curve of the guide polyline, as in
                        EDA "route parallel / bus" tools.
* ``AvoidRouter``     - globally optimal object-avoiding orthogonal route:
                        orthogonal visibility graph + A* on (node, heading)
                        with a bend penalty (Wybrow, Marriott & Stuckey 2009,
                        the algorithm behind libavoid / Inkscape connectors).
"""
from __future__ import annotations

import heapq
from bisect import bisect_left
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from . import geometry as g
from .geometry import Pt, Rect
from .route import Route


@dataclass
class RouteContext:
    """Everything a router may need besides the two end points."""
    obstacles: Sequence[Rect] = ()
    clearance: float = 8.0          # hull inflation around obstacles
    flip: bool = False              # user pressed the posture-toggle key
    prev_heading: Optional[str] = None   # 'H'/'V' of last committed segment
    auto_posture: Optional[str] = None   # 'HV'/'VH' latched by the tool
    angle_step: float = 0.0         # degrees; 0 = free angle
    bend_penalty: float = 40.0      # AvoidRouter: cost of one bend, in px
    max_iterations: int = 64        # HugRouter safety limit
    # --- bus / follow mode -------------------------------------------------
    guides: Sequence[Sequence[Pt]] = ()  # existing nets (polylines) in the scene
    guide: Optional[Sequence[Pt]] = None  # explicit pick; None = nearest to start
    bus_pitch: float = 12.0         # lane spacing between bus members
    bus_capture: float = 60.0       # auto-pick radius around the start point

    def hulls(self, *endpoints: Pt) -> List[Rect]:
        """Inflated obstacles, skipping any that swallow an end point.

        An end point inside a hull means the user is starting from / heading
        into that shape (e.g. a port on its edge), so it must not repel the
        line.
        """
        out = []
        for r in self.obstacles:
            h = g.inflate(r, self.clearance)
            if any(g.contains(h, p, strict=True) for p in endpoints):
                continue
            out.append(h)
        return out


class Router:
    """Base class.

    The contract has two levels, and a router implements whichever it needs:

    ``route()``  the *skeleton*: a polyline ``[start, ..., end]``.  Wrappers
                 (hug, bus), obstacle checks and tests work on this.
    ``shape()``  the *drawn geometry*: a :class:`~smartline.route.Route` that
                 may contain curves.  Defaults to the skeleton as straight
                 lines, so polyline routers only write ``route()``.

    Curve routers override ``shape()`` (and let ``route()`` return the
    flattened curve) - see :class:`CubicRouter`.
    """
    name = "base"
    label = "Base"

    def route(self, start: Pt, end: Pt, ctx: RouteContext) -> List[Pt]:
        raise NotImplementedError

    def shape(self, start: Pt, end: Pt, ctx: RouteContext) -> Route:
        return Route.from_points(self.route(start, end, ctx), router=self.name)

    def constrain_end(self, start: Pt, end: Pt, ctx: RouteContext) -> Pt:
        """Where the committed end point should land (default: the cursor)."""
        return end

    def constrain_start(self, start: Pt, ctx: RouteContext) -> Pt:
        """Where a freshly clicked anchor should land (default: as clicked)."""
        return start


# ---------------------------------------------------------------- (a) straight

class StraightRouter(Router):
    name, label = "straight", "Straight"

    def constrain_end(self, start, end, ctx):
        return g.snap_angle(start, end, ctx.angle_step) if ctx.angle_step else end

    def route(self, start, end, ctx):
        return [start, self.constrain_end(start, end, ctx)]


# -------------------------------------------------------------- (b) orthogonal

class OrthoRouter(Router):
    """Manhattan routing with a toggleable posture.

    ``style="L"``          two segments per click; posture HV (horizontal
                           first) or VH.  The automatic choice is whatever
                           the tool latched from the initial drag direction
                           (``ctx.auto_posture``), else the dominant axis.
                           ``ctx.flip`` inverts the automatic choice.
    ``style="alternate"``  one segment per click, alternating H/V - the
                           behaviour of PictoSync's ORTHOCURVE mode.
    """
    name, label = "ortho", "Orthogonal"

    def __init__(self, style: str = "L"):
        if style not in ("L", "alternate"):
            raise ValueError(style)
        self.style = style

    def posture(self, start: Pt, end: Pt, ctx: RouteContext) -> str:
        dx, dy = abs(end[0] - start[0]), abs(end[1] - start[1])
        p = None
        if self.style == "alternate":
            p = {"H": "VH", "V": "HV"}.get(ctx.prev_heading)
        elif ctx.auto_posture in ("HV", "VH"):
            p = ctx.auto_posture
        if p is None:
            p = "HV" if dx >= dy else "VH"
        if ctx.flip:
            p = "VH" if p == "HV" else "HV"
        return p

    def constrain_end(self, start, end, ctx):
        if self.style == "alternate":
            if self.posture(start, end, ctx) == "HV":
                return (end[0], start[1])
            return (start[0], end[1])
        return end

    def route(self, start, end, ctx):
        if self.style == "alternate":
            return g.simplify([start, self.constrain_end(start, end, ctx)])
        if self.posture(start, end, ctx) == "HV":
            elbow = (end[0], start[1])
        else:
            elbow = (start[0], end[1])
        return g.simplify([start, elbow, end]) if start != end else [start, end]


class OctilinearRouter(Router):
    """45 degree posture: a diagonal run plus an axis-aligned run.

    Default is "diagonal first"; ``ctx.flip`` gives "straight first".
    """
    name, label = "octilinear", "45° (octilinear)"

    def route(self, start, end, ctx):
        dx, dy = end[0] - start[0], end[1] - start[1]
        d = min(abs(dx), abs(dy))
        sx = 1.0 if dx >= 0 else -1.0
        sy = 1.0 if dy >= 0 else -1.0
        if not ctx.flip:                       # diagonal, then straight
            mid = (start[0] + sx * d, start[1] + sy * d)
        else:                                  # straight, then diagonal
            mid = (end[0] - sx * d, end[1] - sy * d)
        pts = g.simplify([start, mid, end])
        return pts if len(pts) >= 2 else [start, end]


# ------------------------------------------------------------- (c) hugging

class HugRouter(Router):
    """Walkaround wrapper: obstacle-hugging version of any base router.

    The base route is kept wherever it is legal.  Each segment that pierces an
    obstacle hull is cut at its entry and exit points and the gap is bridged
    along the hull boundary - clockwise or counter-clockwise, whichever is
    shorter (``ctx.flip`` is forwarded to the base router, so posture toggling
    still works).  New pieces are re-checked against the remaining obstacles,
    with an iteration cap exactly like KiCad's WALKAROUND.
    """

    def __init__(self, base: Optional[Router] = None,
                 name: Optional[str] = None, label: Optional[str] = None):
        self.base = base or StraightRouter()
        self.name = name or f"hug-{self.base.name}"
        self.label = label or f"Hug ({self.base.label})"

    def constrain_end(self, start, end, ctx):
        return self.base.constrain_end(start, end, ctx)

    def route(self, start, end, ctx):
        return self.fix(self.base.route(start, end, ctx), ctx)

    def fix(self, pts: List[Pt], ctx: RouteContext) -> List[Pt]:
        """Walkaround pass over an arbitrary polyline."""
        hulls = ctx.hulls(pts[0], pts[-1])
        if not hulls:
            return pts
        for _ in range(ctx.max_iterations):
            hit = self._first_hit(pts, hulls)
            if hit is None:
                break
            i, h, t_in = hit
            # last place the polyline leaves this hull (an elbow may sit inside)
            k, t_out = i, None
            for m in range(i, len(pts) - 1):
                c = g.clip_segment(pts[m], pts[m + 1], h)
                if c:
                    k, t_out = m, c[1]
            p_in = _lerp(pts[i], pts[i + 1], t_in)
            p_out = _lerp(pts[k], pts[k + 1], t_out)
            cw = [p_in] + g.walk_perimeter(h, p_in, p_out, True) + [p_out]
            ccw = [p_in] + g.walk_perimeter(h, p_in, p_out, False) + [p_out]
            detour = cw if g.length(cw) <= g.length(ccw) else ccw
            pts = g.simplify(pts[:i + 1] + detour + pts[k + 1:])
        return pts

    @staticmethod
    def _first_hit(pts: Sequence[Pt], hulls: Sequence[Rect]):
        """(segment index, hull, t_in) of the first collision along *pts*."""
        for i in range(len(pts) - 1):
            best = None
            for h in hulls:
                c = g.clip_segment(pts[i], pts[i + 1], h)
                if c and (best is None or c[0] < best[2]):
                    best = (i, h, c[0])
            if best:
                return best
        return None


def _lerp(a: Pt, b: Pt, t: float) -> Pt:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


# ------------------------------------------------- (d) globally optimal avoid

_DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))     # E, S, W, N  (index = heading)


class AvoidRouter(Router):
    """Object-avoiding orthogonal routing (Wybrow-Marriott-Stuckey, GD 2009).

    1. *Orthogonal visibility graph*: grid lines through every hull edge and
       both end points; grid nodes inside a hull and grid edges crossing one
       are removed.  (A superset of the paper's OVG - same optimal routes,
       simpler to build; fine for the tens-to-low-hundreds of shapes typical
       of an interactive diagram.)
    2. *A\\** over states ``(node, heading)`` with cost
       ``length + bend_penalty * bends`` and the admissible heuristic
       ``manhattan + bend_penalty * min_bends_remaining``.
    3. Falls back to a hugging orthogonal route if the goal is unreachable.

    ``ctx.flip`` biases the first step vertical instead of horizontal, so the
    posture key still has a visible effect when two routes tie.
    """
    name, label = "avoid", "Auto-avoid (A*)"

    def __init__(self):
        self._fallback = HugRouter(OrthoRouter())

    def route(self, start, end, ctx):
        if g.dist(start, end) < 1e-6:
            return [start, end]
        hulls = ctx.hulls(start, end)
        if not hulls:
            return OrthoRouter().route(start, end, ctx)
        path = self._astar(start, end, hulls, ctx)
        return path if path else self._fallback.route(start, end, ctx)

    # -- graph -------------------------------------------------------------
    @staticmethod
    def _build(start: Pt, end: Pt, hulls: Sequence[Rect]):
        xs = sorted({start[0], end[0], *(h[0] for h in hulls), *(h[2] for h in hulls)})
        ys = sorted({start[1], end[1], *(h[1] for h in hulls), *(h[3] for h in hulls)})
        nx, ny = len(xs), len(ys)
        # blocked_h[j][i]: edge (i,j)-(i+1,j);  blocked_v[j][i]: (i,j)-(i,j+1)
        blocked_h = [bytearray(nx) for _ in range(ny)]
        blocked_v = [bytearray(nx) for _ in range(ny)]
        for l, t, r, b in hulls:
            i0, i1 = bisect_left(xs, l), bisect_left(xs, r)
            j0, j1 = bisect_left(ys, t), bisect_left(ys, b)
            for j in range(j0 + 1, j1):          # rows strictly inside
                row = blocked_h[j]
                for i in range(i0, i1):
                    row[i] = 1
            for j in range(j0, j1):
                row = blocked_v[j]
                for i in range(i0 + 1, i1):      # columns strictly inside
                    row[i] = 1
        return xs, ys, blocked_h, blocked_v

    def _astar(self, start, end, hulls, ctx) -> Optional[List[Pt]]:
        xs, ys, bh, bv = self._build(start, end, hulls)
        nx, ny = len(xs), len(ys)
        si, sj = xs.index(start[0]), ys.index(start[1])
        gi, gj = xs.index(end[0]), ys.index(end[1])
        bp = ctx.bend_penalty
        gx, gy = end

        def h(i, j, d):
            ddx, ddy = gx - xs[i], gy - ys[j]
            est = abs(ddx) + abs(ddy)
            if abs(ddx) > 1e-9 and abs(ddy) > 1e-9:
                est += bp                               # at least one bend
            elif d >= 0:
                ux, uy = _DIRS[d]
                if ux * ddx + uy * ddy < abs(ddx) + abs(ddy) - 1e-9:
                    est += bp                           # not heading at goal
            return est

        tie = 0
        # heading -1 = "none yet"; a tiny bias implements the posture flip.
        open_: List[Tuple[float, int, float, int, int, int]] = [(h(si, sj, -1), 0, 0.0, si, sj, -1)]
        best: Dict[Tuple[int, int, int], float] = {(si, sj, -1): 0.0}
        came: Dict[Tuple[int, int, int], Tuple[int, int, int]] = {}
        while open_:
            _, _, cost, i, j, d = heapq.heappop(open_)
            if cost > best.get((i, j, d), float("inf")) + 1e-9:
                continue
            if i == gi and j == gj:
                pts = [(xs[i], ys[j])]
                key = (i, j, d)
                while key in came:
                    key = came[key]
                    pts.append((xs[key[0]], ys[key[1]]))
                pts.reverse()
                return g.simplify(pts)
            for nd, (ux, uy) in enumerate(_DIRS):
                if d >= 0 and (nd + 2) % 4 == d:
                    continue                            # no U-turns
                ni, nj = i + ux, j + uy
                if not (0 <= ni < nx and 0 <= nj < ny):
                    continue
                if ux:
                    if bh[j][min(i, ni)]:
                        continue
                    step = abs(xs[ni] - xs[i])
                else:
                    if bv[min(j, nj)][i]:
                        continue
                    step = abs(ys[nj] - ys[j])
                ncost = cost + step
                if d >= 0 and nd != d:
                    ncost += bp
                elif d < 0:
                    vertical_first = bool(uy)
                    ncost += 0.0 if vertical_first == ctx.flip else 1e-3
                key = (ni, nj, nd)
                if ncost < best.get(key, float("inf")) - 1e-9:
                    best[key] = ncost
                    came[key] = (i, j, d)
                    tie += 1
                    heapq.heappush(open_, (ncost + h(ni, nj, nd), tie, ncost, ni, nj, nd))
        return None


# ------------------------------------------------------- (e) bus / follow

class BusRouter(Router):
    """Hug an existing *net*: run the new line parallel to a guide polyline.

    This is the "bus" / "follow" / "route parallel" gesture of schematic and
    PCB tools.  Geometrically the lane is a *parallel (offset) curve* of the
    guide with mitered joins, so inner corners shorten and outer corners
    lengthen and the wires stay exactly ``pitch`` apart through every bend.

    Guide selection  ``ctx.guide`` if the tool/user picked one explicitly,
                     otherwise the net in ``ctx.guides`` nearest to the start
                     point, provided it is within ``ctx.bus_capture``.
    Side             the side of the guide the start point is on;
                     ``ctx.flip`` (posture key) switches to the other side.
    Lane             ``round(distance(start, guide) / pitch)`` (min 1): start
                     next to the outermost member and you get the next lane;
                     start three pitches out and you get lane 3.
    Route            start -> jog onto the lane -> along the lane to the point
                     nearest the cursor -> jog off to the cursor.  Jogs are
                     orthogonal and leave the lane at right angles; past the
                     end of the guide the line simply continues straight on.
    Obstacles        optional walkaround post-pass (``hug_obstacles``).
    No guide         falls back to ``fallback`` (orthogonal hugging).
    """
    name, label = "bus", "Bus (follow a net)"

    def __init__(self, fallback: Optional[Router] = None, hug_obstacles: bool = True):
        self.fallback = fallback or HugRouter(OrthoRouter())
        self._hug = HugRouter() if hug_obstacles else None
        self._ortho = OrthoRouter()

    # -- guide / lane ------------------------------------------------------
    def pick_guide(self, start: Pt, ctx: RouteContext) -> Optional[List[Pt]]:
        if ctx.guide is not None:
            gd = g.simplify(ctx.guide)
            return gd if len(gd) >= 2 else None
        best, best_d = None, ctx.bus_capture
        for cand in ctx.guides:
            cand = g.simplify(cand)
            if len(cand) < 2:
                continue
            d = g.project_on_polyline(cand, start)[0]
            if d <= best_d:
                best, best_d = cand, d
        return best

    def lane(self, start: Pt, guide: Sequence[Pt], ctx: RouteContext) -> List[Pt]:
        d = g.project_on_polyline(guide, start)[0]
        n = max(1, round(d / ctx.bus_pitch)) if d <= ctx.bus_capture else 1
        side = g.side_of_polyline(guide, start) * (-1.0 if ctx.flip else 1.0)
        return g.offset_polyline(guide, side * n * ctx.bus_pitch)

    def constrain_start(self, start, ctx):
        """Pull an anchor clicked within half a pitch of a lane onto the lane,
        so bus members start flush instead of with a 1-2 px jog."""
        guide = self.pick_guide(start, ctx)
        if guide is None:
            return start
        lane = self.lane(start, guide, ctx)
        if len(lane) < 2:
            return start
        d, _, _, q = g.project_on_polyline(lane, start)
        return q if d <= ctx.bus_pitch / 2.0 + 1e-9 else start

    # -- route -------------------------------------------------------------
    def route(self, start, end, ctx):
        guide = self.pick_guide(start, ctx)
        if guide is None:
            return self.fallback.route(start, end, ctx)
        lane = self.lane(start, guide, ctx)
        if len(lane) < 2:
            return self.fallback.route(start, end, ctx)
        _, ia, ta, pa = g.project_on_polyline(lane, start)
        _, ib, tb, pb = g.project_on_polyline(lane, end)
        head = self._jog(start, pa, lane, ia, ta, ctx, arriving=True)
        tail = self._jog(pb, end, lane, ib, tb, ctx, arriving=False)
        pts = g.simplify(head + g.subpath(lane, ia, ta, ib, tb) + tail)
        if len(pts) < 2:
            return [start, end]
        if pts[0] != start:                       # simplify() may eat a spike
            pts.insert(0, start)
        return self._hug.fix(pts, ctx) if self._hug else pts

    def _jog(self, a: Pt, b: Pt, lane, i: int, t: float, ctx, arriving: bool) -> List[Pt]:
        """Orthogonal connection between a free point and a lane point."""
        if g.dist(a, b) < 1e-6:
            return [a]
        h = g.heading(lane[i], lane[i + 1])
        if h is None:                             # diagonal lane: go direct
            return [a, b]
        at_end = (i == 0 and t <= 1e-9) or (i == len(lane) - 2 and t >= 1 - 1e-9)
        # On the lane: meet it at right angles.  Past its end: carry straight on.
        first_perp = not at_end
        if arriving:                              # last leg touches the lane
            posture = ("HV" if h == "H" else "VH") if first_perp else ("VH" if h == "H" else "HV")
        else:                                     # first leg leaves the lane
            posture = ("VH" if h == "H" else "HV") if first_perp else ("HV" if h == "H" else "VH")
        return self._ortho.route(a, b, replace(ctx, auto_posture=posture, flip=False))


# ------------------------------------------------------------- (f) cubic

class CubicRouter(Router):
    """Smooth cubic-Bezier connector on top of any skeleton router.

    This is the reference implementation of the *curve* side of the framework
    and the intended override point for applications with their own curve
    logic: subclass it, override :meth:`smooth` (skeleton -> Route), and
    register the subclass under the same name with ``replace=True``.

    Default behaviour
      * 2-point skeleton  -> one "S" curve with axis-aligned end tangents, the
        node-editor style connector.  Tangents are horizontal or vertical
        following the same posture logic as :class:`OrthoRouter`, so the
        posture key flips them.
      * longer skeleton   -> ``style="blend"`` (default): straight runs with
        every corner replaced by a cubic blend that uses up to half of each
        adjacent run - the curve never strays outside the skeleton's corners,
        so a hugging/avoiding base stays clear of shapes:
        ``CubicRouter(HugRouter(OrthoRouter()))``.
        ``style="spline"``: Catmull-Rom through the vertices (C1, but it
        overshoots corners - use it for free-form skeletons, not around shapes).
    """
    name, label = "cubic", "Cubic curve"

    def __init__(self, base: Optional[Router] = None, tension: float = 0.5,
                 style: str = "blend", max_radius: float = 60.0):
        if style not in ("blend", "spline"):
            raise ValueError(style)
        self.base = base or StraightRouter()
        self.tension = tension
        self.style = style
        self.max_radius = max_radius
        self._posture = OrthoRouter()

    def constrain_end(self, start, end, ctx):
        return self.base.constrain_end(start, end, ctx)

    def constrain_start(self, start, ctx):
        return self.base.constrain_start(start, ctx)

    def route(self, start, end, ctx):
        return self.shape(start, end, ctx).flatten()

    def shape(self, start, end, ctx):
        skeleton = self.base.route(start, end, ctx)
        if len(skeleton) < 2 or g.dist(skeleton[0], skeleton[-1]) < 1e-6:
            return Route.from_points([start, end], router=self.name)
        r = self.smooth(skeleton, ctx)
        r.meta.setdefault("router", self.name)
        return r

    # -- override point ----------------------------------------------------
    def smooth(self, skeleton: Sequence[Pt], ctx: RouteContext) -> Route:
        if len(skeleton) == 2:
            a, b = skeleton
            k = self.tension
            if self._posture.posture(a, b, ctx) == "HV":
                d = (b[0] - a[0]) * k
                c1, c2 = (a[0] + d, a[1]), (b[0] - d, b[1])
            else:
                d = (b[1] - a[1]) * k
                c1, c2 = (a[0], a[1] + d), (b[0], b[1] - d)
            return Route(a).cubic_to(c1, c2, b)
        if self.style == "spline":
            return catmull_rom(skeleton, self.tension)
        # A blend of radius r cuts 0.41 r inside a corner; a hull corner is
        # 1.41 x clearance from the shape - so r <= 3 x clearance stays clear.
        r = min(self.max_radius, 3.0 * ctx.clearance) if ctx.obstacles else self.max_radius
        return blend_corners(skeleton, r)


def blend_corners(pts: Sequence[Pt], max_radius: float = 60.0) -> Route:
    """Straight runs joined by cubic corner blends (circular-arc-like, G1)."""
    pts = g.simplify(pts)
    out = Route(pts[0])
    kappa = 0.5523                       # cubic approximation of a quarter circle
    for i in range(1, len(pts) - 1):
        a, v, b = pts[i - 1], pts[i], pts[i + 1]
        la, lb = g.dist(a, v), g.dist(v, b)
        r = min(max_radius, la / 2.0, lb / 2.0)
        p1 = (v[0] + (a[0] - v[0]) * r / la, v[1] + (a[1] - v[1]) * r / la)
        p2 = (v[0] + (b[0] - v[0]) * r / lb, v[1] + (b[1] - v[1]) * r / lb)
        if g.dist(out.end, p1) > 1e-6:
            out.line_to(p1)
        out.cubic_to((p1[0] + (v[0] - p1[0]) * kappa, p1[1] + (v[1] - p1[1]) * kappa),
                     (p2[0] + (v[0] - p2[0]) * kappa, p2[1] + (v[1] - p2[1]) * kappa), p2)
    if g.dist(out.end, pts[-1]) > 1e-6:
        out.line_to(pts[-1])
    return out


def catmull_rom(pts: Sequence[Pt], tension: float = 0.5) -> Route:
    """Cubic Bezier chain through every point of *pts* (C1 continuous)."""
    pts = list(pts)
    ext = [pts[0]] + pts + [pts[-1]]
    k = tension / 3.0 * 2.0            # tension 0.5 -> the classic 1/6 rule x2
    out = Route(pts[0])
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        c1 = (p1[0] + (p2[0] - p0[0]) * k / 2, p1[1] + (p2[1] - p0[1]) * k / 2)
        c2 = (p2[0] - (p3[0] - p1[0]) * k / 2, p2[1] - (p3[1] - p1[1]) * k / 2)
        out.cubic_to(c1, c2, p2)
    return out
