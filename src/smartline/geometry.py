"""Pure-Python geometry helpers for smartline (no Qt imports).

Everything here works on plain tuples so the routing core can be unit-tested
and reused without a GUI:

    Pt   = (x, y)
    Rect = (left, top, right, bottom)      # left < right, top < bottom
"""
from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

Pt = Tuple[float, float]
Rect = Tuple[float, float, float, float]

EPS = 1e-9


# --------------------------------------------------------------------- rects

def inflate(r: Rect, d: float) -> Rect:
    """Return *r* grown by *d* on every side (the obstacle's routing "hull")."""
    return (r[0] - d, r[1] - d, r[2] + d, r[3] + d)


def contains(r: Rect, p: Pt, strict: bool = True) -> bool:
    """True if *p* is inside *r*; ``strict`` excludes the boundary."""
    if strict:
        return r[0] + EPS < p[0] < r[2] - EPS and r[1] + EPS < p[1] < r[3] - EPS
    return r[0] - EPS <= p[0] <= r[2] + EPS and r[1] - EPS <= p[1] <= r[3] + EPS


def clip_segment(a: Pt, b: Pt, r: Rect) -> Optional[Tuple[float, float]]:
    """Liang-Barsky clip of segment a->b against the *open* interior of *r*.

    Returns ``(t_in, t_out)`` with ``0 <= t_in < t_out <= 1`` if the segment
    passes through the interior, else ``None``.  Segments that merely touch
    or slide along the boundary do not count as intersecting, which is what
    lets a route "hug" a hull without being flagged as colliding with it.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - r[0]), (dx, r[2] - a[0]),
                 (-dy, a[1] - r[1]), (dy, r[3] - a[1])):
        if abs(p) < EPS:
            if q <= EPS:            # parallel and outside / on the boundary
                return None
        else:
            t = q / p
            if p < 0:
                if t > t1:
                    return None
                t0 = max(t0, t)
            else:
                if t < t0:
                    return None
                t1 = min(t1, t)
    if t1 - t0 <= EPS:
        return None
    # The midpoint of the clipped span must be strictly inside.
    tm = (t0 + t1) / 2.0
    if not contains(r, (a[0] + dx * tm, a[1] + dy * tm), strict=True):
        return None
    return t0, t1


def segment_hits(a: Pt, b: Pt, r: Rect) -> bool:
    return clip_segment(a, b, r) is not None


# ----------------------------------------------------------------- perimeter

def _perimeter_pos(r: Rect, p: Pt) -> float:
    """Distance of boundary point *p* along the perimeter, clockwise on screen
    (y down) starting at the top-left corner."""
    l, t, rt, b = r
    w, h = rt - l, b - t
    x = min(max(p[0], l), rt)
    y = min(max(p[1], t), b)
    d = [abs(y - t), abs(x - rt), abs(y - b), abs(x - l)]   # top,right,bottom,left
    side = d.index(min(d))
    if side == 0:
        return x - l
    if side == 1:
        return w + (y - t)
    if side == 2:
        return w + h + (rt - x)
    return 2 * w + h + (b - y)


def walk_perimeter(r: Rect, p_in: Pt, p_out: Pt, clockwise: bool) -> List[Pt]:
    """Boundary path from *p_in* to *p_out* around *r* in the given winding.

    Returns the intermediate hull corners only (``p_in``/``p_out`` excluded).
    """
    l, t, rt, b = r
    w, h = rt - l, b - t
    total = 2 * (w + h)
    corners = [(0.0, (l, t)), (w, (rt, t)), (w + h, (rt, b)), (2 * w + h, (l, b))]
    s0, s1 = _perimeter_pos(r, p_in), _perimeter_pos(r, p_out)
    out: List[Tuple[float, Pt]] = []
    for pos, c in corners:
        if clockwise:
            d, span = (pos - s0) % total, (s1 - s0) % total
        else:
            d, span = (s0 - pos) % total, (s0 - s1) % total
        if EPS < d < span - EPS:
            out.append((d, c))
    out.sort(key=lambda e: e[0])
    return [c for _, c in out]


# ----------------------------------------------------------------- polylines

def dist(a: Pt, b: Pt) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def length(pts: Sequence[Pt]) -> float:
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def bends(pts: Sequence[Pt]) -> int:
    return max(0, len(simplify(pts)) - 2)


def simplify(pts: Iterable[Pt], tol: float = 1e-6) -> List[Pt]:
    """Drop duplicate points, collinear interior points and out-and-back spikes."""
    out: List[Pt] = []
    for p in pts:
        if out and dist(out[-1], p) <= tol:
            continue
        out.append((float(p[0]), float(p[1])))
        while len(out) >= 3:
            a, m, c = out[-3], out[-2], out[-1]
            cross = (m[0] - a[0]) * (c[1] - m[1]) - (m[1] - a[1]) * (c[0] - m[0])
            if abs(cross) > tol * max(1.0, dist(a, m) + dist(m, c)):
                break
            del out[-2]                       # collinear (or a spike): drop m
            if dist(out[-2], out[-1]) <= tol:  # spike collapsed onto a
                del out[-1]
                break
    return out


def snap_to_grid(p: Pt, grid: float) -> Pt:
    if grid <= 0:
        return p
    return (round(p[0] / grid) * grid, round(p[1] / grid) * grid)


def snap_angle(a: Pt, p: Pt, step_deg: float) -> Pt:
    """Project *p* so that a->p lies on the nearest multiple of *step_deg*."""
    dx, dy = p[0] - a[0], p[1] - a[1]
    r = math.hypot(dx, dy)
    if r < EPS or step_deg <= 0:
        return p
    step = math.radians(step_deg)
    ang = round(math.atan2(dy, dx) / step) * step
    # keep the projection of the cursor onto the snapped ray, not the radius
    ux, uy = math.cos(ang), math.sin(ang)
    proj = max(0.0, dx * ux + dy * uy)
    return (a[0] + ux * proj, a[1] + uy * proj)


def heading(a: Pt, b: Pt) -> Optional[str]:
    """'H', 'V' or None (diagonal / zero length) for the segment a->b."""
    dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
    if dx < 1e-6 and dy < 1e-6:
        return None
    if dy < 1e-6:
        return "H"
    if dx < 1e-6:
        return "V"
    return None


# ------------------------------------------------- parallel curves (bus lanes)

def project_on_polyline(pts: Sequence[Pt], p: Pt) -> Tuple[float, int, float, Pt]:
    """Nearest point of polyline *pts* to *p*.

    Returns ``(distance, segment_index, t, point)`` with ``0 <= t <= 1``.
    """
    best = (float("inf"), 0, 0.0, pts[0])
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 < EPS else min(1.0, max(0.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
        q = (a[0] + dx * t, a[1] + dy * t)
        d = dist(p, q)
        if d < best[0] - 1e-9:
            best = (d, i, t, q)
    return best


def side_of_polyline(pts: Sequence[Pt], p: Pt) -> float:
    """+1 / -1: which side of the (directed) polyline *p* lies on; +1 is the
    side the normal ``(-dy, dx)`` points to.  0-distance counts as +1."""
    _, i, _, _ = project_on_polyline(pts, p)
    a, b = pts[i], pts[i + 1]
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    return -1.0 if cross < 0 else 1.0


def offset_polyline(pts: Sequence[Pt], d: float, miter_limit: float = 4.0) -> List[Pt]:
    """Parallel curve of an open polyline at signed distance *d* (mitered joins).

    Segments that would invert on the inside of a tight corner (shorter than the
    offset can accommodate) are dropped and their neighbours re-joined, so the
    result never loops back on itself for the staircase shapes wires have.
    Very sharp joins are bevelled once the miter exceeds ``miter_limit * |d|``.
    """
    pts = simplify(pts)
    if len(pts) < 2 or abs(d) < EPS:
        return list(pts)
    lines = []                                   # (a_off, b_off, unit dir)
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        l = dist(a, b)
        ux, uy = (b[0] - a[0]) / l, (b[1] - a[1]) / l
        nx, ny = -uy * d, ux * d
        lines.append(((a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (ux, uy)))

    def join(l1, l2) -> Pt:
        (_, b1, u1), (a2, _, u2) = l1, l2
        den = u1[0] * u2[1] - u1[1] * u2[0]
        if abs(den) < 1e-9:                      # parallel: nothing to intersect
            return b1
        s = ((a2[0] - b1[0]) * u2[1] - (a2[1] - b1[1]) * u2[0]) / den
        return (b1[0] + u1[0] * s, b1[1] + u1[1] * s)

    while True:
        verts = [lines[0][0]] + [join(lines[i], lines[i + 1]) for i in range(len(lines) - 1)] + [lines[-1][1]]
        flipped = [i for i, (_, _, u) in enumerate(lines)
                   if (verts[i + 1][0] - verts[i][0]) * u[0] + (verts[i + 1][1] - verts[i][1]) * u[1] < -1e-9]
        if not flipped or len(lines) == 1:
            break
        del lines[flipped[0]]

    out: List[Pt] = [verts[0]]
    for i in range(1, len(verts) - 1):           # bevel over-long miters
        corner = lines[i - 1][1]
        if dist(verts[i], corner) > miter_limit * abs(d):
            out.extend([lines[i - 1][1], lines[i][0]])
        else:
            out.append(verts[i])
    out.append(verts[-1])
    return simplify(out)


def subpath(pts: Sequence[Pt], i0: int, t0: float, i1: int, t1: float) -> List[Pt]:
    """Piece of a polyline between two ``(segment, t)`` positions, in travel
    order (runs backwards when the second position precedes the first)."""
    def at(i, t):
        a, b = pts[i], pts[i + 1]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    if (i0, t0) <= (i1, t1):
        mid = list(pts[i0 + 1:i1 + 1])
    else:
        mid = list(reversed(pts[i1 + 1:i0 + 1]))
    return simplify([at(i0, t0)] + mid + [at(i1, t1)])


# ------------------------------------------------------- line-on-line overlap

def overlap_length(a: Sequence[Pt], b: Sequence[Pt], tol: float) -> float:
    """Length of polyline *a* that runs *along* polyline *b*: parallel to one of
    its segments and closer than *tol*.  Crossings do not count - only shared
    track does, which is what makes two lines unreadable."""
    total = 0.0
    for i in range(len(a) - 1):
        p0, p1 = a[i], a[i + 1]
        la = dist(p0, p1)
        if la < 1e-9:
            continue
        ux, uy = (p1[0] - p0[0]) / la, (p1[1] - p0[1]) / la
        covered: List[Tuple[float, float]] = []
        for j in range(len(b) - 1):
            q0, q1 = b[j], b[j + 1]
            lb = dist(q0, q1)
            if lb < 1e-9:
                continue
            if abs(ux * (q1[1] - q0[1]) - uy * (q1[0] - q0[0])) / lb > 0.05:
                continue                                   # not parallel (about 3 degrees)
            d0 = abs(ux * (q0[1] - p0[1]) - uy * (q0[0] - p0[0]))
            d1 = abs(ux * (q1[1] - p0[1]) - uy * (q1[0] - p0[0]))
            if max(d0, d1) >= tol:
                continue
            s0 = ux * (q0[0] - p0[0]) + uy * (q0[1] - p0[1])
            s1 = ux * (q1[0] - p0[0]) + uy * (q1[1] - p0[1])
            lo, hi = max(0.0, min(s0, s1)), min(la, max(s0, s1))
            if hi - lo > 1e-9:
                covered.append((lo, hi))
        covered.sort()
        end = -1.0
        for lo, hi in covered:                             # union, so nothing is counted twice
            if hi > end:
                total += hi - max(lo, end)
                end = hi
    return total


def shortcut(pts: Sequence[Pt], hulls: Sequence[Rect]) -> List[Pt]:
    """Pull a polyline taut: drop every vertex that can be skipped without the
    line passing through a hull.  Turns a rectilinear walkaround into the
    corner-to-corner detour a free-angle line should have."""
    pts = list(pts)
    out, i = [pts[0]], 0
    while i < len(pts) - 1:
        j = len(pts) - 1
        while j > i + 1 and any(segment_hits(pts[i], pts[j], h) for h in hulls):
            j -= 1
        out.append(pts[j])
        i = j
    return simplify(out)
