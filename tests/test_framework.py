"""Route type, registry / override mechanism, cubic router - no Qt required."""
import pytest

import smartline as sl
from smartline import CubicRouter, Route, RouteContext, Router, geometry as g

BOX = (100, 100, 200, 200)


def test_route_views_roundtrip():
    r = Route((0, 0)).line_to((10, 0)).cubic_to((20, 0), (20, 10), (30, 10)).quad_to((40, 10), (40, 20))
    assert not r.is_polyline and r.end == (40, 20)
    assert r.anchors() == [(0, 0), (10, 0), (30, 10), (40, 20)]
    assert r.to_svg() == "M 0 0 L 10 0 C 20 0 20 10 30 10 Q 40 10 40 20"
    flat = r.flatten()
    assert flat[0] == (0, 0) and flat[-1] == (40, 20) and len(flat) > 8
    assert [n["cmd"] for n in r.to_nodes()] == ["M", "L", "C", "Q"]
    assert set(r.to_nodes()[2]) == {"cmd", "x", "y", "c1x", "c1y", "c2x", "c2y"}


def test_route_nodes_normalized_and_hv():
    r = Route.from_points([(10, 10), (110, 10), (110, 60)])
    assert r.bbox() == (10, 10, 110, 60)
    assert r.to_nodes(normalize=True, hv=True) == [
        {"cmd": "M", "x": 0.0, "y": 0.0}, {"cmd": "H", "x": 1.0}, {"cmd": "V", "y": 1.0}]
    assert r.to_segments() == [((10, 10), (110, 10)), ((110, 10), (110, 60))]   # KiCad wires


def test_every_builtin_honours_the_two_level_contract():
    ctx = RouteContext(obstacles=[BOX])
    for name in sl.available():
        router = sl.create(name)
        assert router.name == name
        skel = router.route((0, 150), (300, 170), ctx)
        shape = router.shape((0, 150), (300, 170), ctx)
        assert isinstance(shape, Route) and shape.start == (0, 150)
        assert g.dist(shape.end, skel[-1]) < 1e-6


def test_cubic_s_curve_and_posture_flip():
    c = CubicRouter()
    assert c.shape((0, 0), (100, 60), RouteContext()).to_svg() == "M 0 0 C 50 0 50 60 100 60"
    assert c.shape((0, 0), (100, 60), RouteContext(flip=True)).to_svg() == "M 0 0 C 0 30 100 30 100 60"


def test_cubic_over_hug_stays_clear_of_shapes():
    ctx = RouteContext(obstacles=[BOX], clearance=8)
    flat = sl.create("cubic-hug").route((0, 150), (300, 300), ctx)
    assert not any(g.segment_hits(flat[i], flat[i + 1], BOX) for i in range(len(flat) - 1))


def test_register_new_mode_and_refuse_silent_override():
    @sl.register
    class Zig(Router):
        name, label = "zig", "Zig"
        def route(self, start, end, ctx):
            return [start, ((start[0] + end[0]) / 2, start[1] - 20), end]
    try:
        assert "zig" in sl.available()
        assert sl.default_routers(["ortho", "zig"])[1].route((0, 0), (10, 0), RouteContext())[1] == (5, -20)
        with pytest.raises(KeyError):
            sl.register("zig", Zig)
    finally:
        sl.unregister("zig")


def test_override_builtin_cubic_like_pictosync_would():
    original = sl.registry._REGISTRY["cubic"]

    @sl.register(replace=True)
    class PictoCubic(CubicRouter):                   # keeps name "cubic"
        def smooth(self, skeleton, ctx):
            a, b = skeleton[0], skeleton[-1]
            return Route(a, meta={"by": "picto"}).cubic_to(a, b, b)
    try:
        r = sl.create("cubic").shape((0, 0), (10, 10), RouteContext())
        assert r.meta["by"] == "picto" and r.meta["router"] == "cubic"
    finally:
        sl.register("cubic", original, replace=True)
