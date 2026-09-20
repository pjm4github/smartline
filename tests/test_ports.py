"""Port exits and collision priorities - no Qt required."""
import smartline as sl
from smartline import Route, RouteContext, edit, geometry as g, ports, reroute, tidy

A = (100, 100, 200, 200)            # shape with a port in the middle of its right side
B = (400, 300, 500, 400)
PORT_A, PORT_B = (200, 150), (400, 350)


def test_port_exit_finds_side_normal_and_stub():
    stub, n, rect = g.port_exit(PORT_A, [A, B], 12)
    assert stub == (212, 150) and n == (1.0, 0.0) and rect == A
    assert g.port_exit((150, 100.8), [A], 12)[:2] == ((150, 88), (0.0, -1.0))      # pen-width slack
    assert g.port_exit((150, 150), [A], 12) is None and g.port_exit((300, 300), [A], 12) is None


def test_every_mode_leaves_and_arrives_squarely_for_the_clearance():
    ctx = RouteContext(obstacles=[A, B], clearance=12)
    find = ports.rect_exit_finder([A, B])
    ea, eb = find(PORT_A, 12), find(PORT_B, 12)
    for name in sl.available():
        r = ports.shape_with_exits(sl.create(name), PORT_A, PORT_B, ctx, ea, eb)
        flat = r.flatten()
        assert flat[0] == PORT_A and flat[-1] == PORT_B, name
        assert ports.stubs_ok(flat, 12, ea, eb), (name, r.to_svg(0))
        assert ports.obstacle_hits(flat, [A, B], ea, eb) == 0 or name in ("straight", "ortho", "octilinear",
                                                                         "ortho-alternate", "cubic"), name


def test_target_behind_the_port_goes_round_its_own_shape():
    ctx = RouteContext(obstacles=[A], clearance=10)
    ea = ports.rect_exit_finder([A])(PORT_A, 10)
    r = ports.shape_with_exits(sl.create("hug"), PORT_A, (0, 150), ctx, ea, None)
    flat = r.flatten()
    assert flat[1] == (210, 150) and ports.obstacle_hits(flat, [A], ea) == 0 and edit.is_orthogonal(r)


def test_cubic_is_tangent_to_both_port_normals():
    ctx = RouteContext(obstacles=[A, B], clearance=10)
    find = ports.rect_exit_finder([A, B])
    ea, eb = find(PORT_A, 10), find((450, 300), 10)                # right side -> top side
    r = ports.shape_with_exits(sl.create("cubic"), PORT_A, (450, 300), ctx, ea, eb)
    assert [s.cmd for s in r.segs] == ["L", "C", "L"]
    c1, c2, _ = r.segs[1].pts
    assert c1[1] == 150 and c1[0] > 210 and c2[0] == 450 and c2[1] < 290   # along +x, then down into the top


def test_refresh_gives_attached_lines_square_exits():
    sloppy = Route.from_points([PORT_A, (205, 250), (400, 250), PORT_B], router="ortho")
    ctx = RouteContext(obstacles=[A, B], clearance=12)
    out = reroute.refresh(sloppy, ctx)
    find = ports.rect_exit_finder([A, B])
    assert ports.stubs_ok(out.flatten(), 12, find(PORT_A, 12), find(PORT_B, 12)), out.to_svg(0)
    assert out.start == PORT_A and out.end == PORT_B and edit.is_orthogonal(out)


def test_unkink_never_shortens_a_port_stub():
    ctx = RouteContext(obstacles=[A], clearance=12)
    line = Route.from_points([PORT_A, (212, 150), (212, 300), (230, 300), (230, 500)])   # stub, then an 18 px jog
    out = tidy.unkink(line, ctx)
    flat = (out or line).flatten()
    assert ports.stubs_ok(flat, 12, ports.rect_exit_finder([A])(PORT_A, 12))


# ------------------------------------------------------------------ priorities

def test_shape_beats_line_and_line_beats_length():
    box = (200, 100, 300, 200)
    fence = [(150, 60), (350, 60)]                     # a line just above the box: going over means crossing twice
    line = Route.from_points([(0, 150), (500, 150)], router="hug")
    ctx = RouteContext(obstacles=[box], clearance=10, wire_spacing=10)
    alone = reroute.repair(line, box, ctx)
    assert min(p[1] for p in alone.anchors()) == 90                       # shorter way: over the top
    fenced = reroute.repair(line, box, ctx, others=[[(150, 95), (150, 60), (350, 60), (350, 95)]])
    assert not reroute.hits(fenced, box)
    assert sum(g.crossings(fenced.flatten(), o) for o in [[(150, 95), (150, 60), (350, 60), (350, 95)]]) == 0
    # boxed in on both sides: it must cross a line rather than touch the shape
    cage = [[(150, 0), (150, 300)], fence]
    forced = reroute.repair(line, box, ctx, others=cage)
    assert not reroute.hits(forced, box) and forced.meta.get("crossings", 0) >= 1


def test_avoid_router_pays_to_not_cross_a_line():
    wall = [(250, -200), (250, 120)]                   # a line hanging down into the direct path
    free = sl.create("avoid").route((0, 0), (500, 0), RouteContext(obstacles=[(240, 300, 260, 320)]))
    assert free == [(0, 0), (500, 0)]
    ctx = RouteContext(obstacles=[(200, 130, 300, 160)], clearance=10, avoid_lines=[wall])
    dodge = sl.create("avoid").route((0, 0), (500, 0), ctx)
    assert g.crossings(dodge, wall) == 0 and len(dodge) > 2
