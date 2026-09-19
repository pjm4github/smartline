"""Core routing tests - no Qt required.  Run:  python -m pytest tests"""
import random

from smartline import geometry as g
from smartline import default_routers
from smartline.routers import (AvoidRouter, BusRouter, HugRouter, OctilinearRouter,
                               OrthoRouter, RouteContext, StraightRouter)

BOX = (100, 100, 200, 200)


def clear_of(pts, ctx):
    hulls = ctx.hulls(pts[0], pts[-1])
    return not any(g.segment_hits(pts[i], pts[i + 1], h)
                   for i in range(len(pts) - 1) for h in hulls)


def is_ortho(pts):
    return all(g.heading(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def test_straight_and_angle_snap():
    ctx = RouteContext(angle_step=45)
    pts = StraightRouter().route((0, 0), (100, 8), ctx)
    assert pts[0] == (0, 0) and abs(pts[1][1]) < 1e-9 and abs(pts[1][0] - 100) < 1e-9


def test_ortho_posture_toggle():
    r = OrthoRouter()
    assert r.route((0, 0), (100, 50), RouteContext())[1] == (100, 0)          # HV
    assert r.route((0, 0), (100, 50), RouteContext(flip=True))[1] == (0, 50)  # VH
    assert r.route((0, 0), (100, 50), RouteContext(auto_posture="VH"))[1] == (0, 50)


def test_ortho_alternate_matches_pictosync():
    r = OrthoRouter("alternate")
    assert r.route((0, 0), (100, 30), RouteContext()) == [(0, 0), (100, 0)]
    assert r.route((100, 0), (150, 80), RouteContext(prev_heading="H")) == [(100, 0), (100, 80)]


def test_octilinear():
    pts = OctilinearRouter().route((0, 0), (100, 30), RouteContext())
    assert pts == [(0, 0), (30, 30), (100, 30)]


def test_hug_straight_goes_around():
    ctx = RouteContext(obstacles=[BOX], clearance=10)
    pts = HugRouter(StraightRouter()).route((0, 150), (300, 160), ctx)
    assert len(pts) > 2 and clear_of(pts, ctx)
    assert pts[0] == (0, 150) and pts[-1] == (300, 160)
    # it hugs: some vertex sits exactly on the hull
    hull = g.inflate(BOX, 10)
    assert any(p[1] in (hull[1], hull[3]) for p in pts[1:-1])


def test_hug_ortho_elbow_inside_obstacle():
    ctx = RouteContext(obstacles=[BOX], clearance=10)
    pts = HugRouter(OrthoRouter()).route((0, 150), (150, 300), ctx)   # elbow (150,150)
    assert clear_of(pts, ctx) and is_ortho(pts)


def test_hug_untouched_when_clear():
    ctx = RouteContext(obstacles=[BOX])
    assert HugRouter(OrthoRouter()).route((0, 0), (50, 50), ctx) == [(0, 0), (50, 0), (50, 50)]


def test_endpoint_inside_obstacle_is_not_repelled():
    ctx = RouteContext(obstacles=[BOX])
    assert HugRouter(StraightRouter()).route((150, 150), (400, 150), ctx) == [(150, 150), (400, 150)]


def test_avoid_is_optimal_simple():
    ctx = RouteContext(obstacles=[BOX], clearance=10, bend_penalty=40)
    pts = AvoidRouter().route((0, 150), (300, 150), ctx)
    assert clear_of(pts, ctx) and is_ortho(pts)
    assert g.bends(pts) == 2 and abs(g.length(pts) - (300 + 2 * 60)) < 1e-6


def test_avoid_prefers_fewer_bends():
    ctx = RouteContext(obstacles=[BOX], clearance=10)
    pts = AvoidRouter().route((0, 0), (300, 300), ctx)
    assert g.bends(pts) == 1 and clear_of(pts, ctx)


def test_fuzz_all_routers():
    rnd = random.Random(7)
    for _ in range(300):
        obs = []
        for _ in range(rnd.randint(1, 12)):
            x, y = rnd.uniform(0, 700), rnd.uniform(0, 500)
            obs.append((x, y, x + rnd.uniform(20, 160), y + rnd.uniform(20, 120)))
        ctx = RouteContext(obstacles=obs, clearance=6, flip=rnd.random() < .5)
        a = (rnd.uniform(-50, 850), rnd.uniform(-50, 650))
        b = (rnd.uniform(-50, 850), rnd.uniform(-50, 650))
        for r in default_routers():
            pts = r.route(a, b, ctx)
            assert pts[0] == a and len(pts) >= 2
            assert g.dist(pts[-1], r.constrain_end(a, b, ctx)) < 1e-6
            if r.name == "avoid":
                assert clear_of(pts, ctx) or not ctx.hulls(a, b)
                assert is_ortho(pts)


# ------------------------------------------------------------------- bus mode
GUIDE = [(100, 100), (400, 100), (400, 300), (700, 300)]


def min_gap(pts, guide, step=5.0):
    """Smallest distance from sampled points of *pts* to the guide."""
    best = 1e9
    for i in range(len(pts) - 1):
        n = max(1, int(g.dist(pts[i], pts[i + 1]) / step))
        for k in range(n + 1):
            p = (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * k / n,
                 pts[i][1] + (pts[i + 1][1] - pts[i][1]) * k / n)
            best = min(best, g.project_on_polyline(guide, p)[0])
    return best


def test_offset_polyline_is_parallel_and_mitered():
    assert g.offset_polyline(GUIDE, 12) == [(100, 112), (388, 112), (388, 312), (700, 312)]
    assert g.offset_polyline(GUIDE, -12) == [(100, 88), (412, 88), (412, 288), (700, 288)]
    # a jog shorter than the offset must not fold back on itself
    out = g.offset_polyline([(0, 0), (100, 0), (100, 5), (200, 5)], 10)
    assert all(out[i + 1][0] >= out[i][0] - 1e-9 for i in range(len(out) - 1))


def test_bus_follows_guide_at_pitch():
    ctx = RouteContext(guides=[GUIDE], bus_pitch=12)
    pts = BusRouter().route((100, 112), (650, 340), ctx)
    assert pts[:4] == [(100, 112), (388, 112), (388, 312), (650, 312)]
    assert pts[-1] == (650, 340) and is_ortho(pts)
    assert abs(min_gap(pts[:4], GUIDE) - 12) < 1e-6


def test_bus_flip_side_and_lane_number():
    r = BusRouter()
    flipped = r.route((100, 112), (650, 300), RouteContext(guides=[GUIDE], flip=True))
    assert (412, 88) in flipped                                   # other side
    lane3 = r.route((100, 135), (650, 300), RouteContext(guides=[GUIDE], bus_pitch=12))
    assert (364, 136) in lane3                                    # 3 x 12 px out


def test_bus_constrain_start_and_fallback():
    r, ctx = BusRouter(), RouteContext(guides=[GUIDE], bus_pitch=12)
    assert r.constrain_start((150, 115), ctx) == (150, 112)       # pulled onto lane
    far = r.route((0, 600), (300, 500), ctx)                      # nothing in range
    assert far == HugRouter(OrthoRouter()).route((0, 600), (300, 500), ctx)


def test_bus_explicit_guide_and_reverse_travel():
    other = [(0, 500), (800, 500)]
    ctx = RouteContext(guides=[GUIDE, other], guide=other, bus_pitch=12)
    pts = BusRouter().route((700, 480), (100, 470), ctx)
    assert pts == [(700, 480), (700, 476), (100, 476), (100, 470)]   # lane 2, travelling backwards


def test_bus_stack_of_members_never_touch():
    nets, r = [GUIDE], BusRouter(hug_obstacles=False)
    for k in range(1, 6):
        ctx = RouteContext(guides=nets, bus_pitch=12)
        start = r.constrain_start((100, 100 + 12 * k + 2), ctx)
        new = r.route(start, (700, 300 + 12 * k), ctx)
        assert abs(min_gap(new, nets[-1]) - 12) < 1e-6, (k, new)
        nets.append(new)
