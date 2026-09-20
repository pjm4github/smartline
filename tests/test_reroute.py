"""Repairing lines around a shape that now overlaps them - no Qt required."""
import smartline as sl
from smartline import Route, RouteContext, edit, geometry as g, reroute

BOX = (100, 100, 200, 200)
CTX = RouteContext(obstacles=[BOX], clearance=10)


def drawn(name, a, b, **ctx):
    r = sl.create(name).shape(a, b, RouteContext(**ctx))
    r.meta.update(router=name, flip=ctx.get("flip", False), posture=None)
    return r


def clear(route, rect=BOX):
    return not reroute.hits(route, rect)


def is_ortho(route):
    return edit.is_orthogonal(route)


def test_untouched_line_returns_none():
    assert reroute.repair(drawn("ortho", (0, 0), (50, 50)), BOX, CTX) is None


def test_ortho_line_stays_orthogonal_and_goes_around():
    line = drawn("ortho", (0, 150), (300, 150))                 # straight through the box
    fixed = reroute.repair(line, BOX, CTX)
    assert clear(fixed) and is_ortho(fixed) and "unresolved" not in fixed.meta
    assert fixed.start == (0, 150) and fixed.end == (300, 150)
    assert any(p[1] in (90, 210) for p in fixed.anchors())      # it hugs the clearance hull


def test_straight_line_gets_a_walkaround_not_a_manhattan_route():
    fixed = reroute.repair(drawn("straight", (0, 120), (300, 190)), BOX, CTX)
    assert clear(fixed) and not is_ortho(fixed)                 # still diagonal outside the detour


def test_cubic_line_stays_a_curve():
    fixed = reroute.repair(drawn("cubic", (0, 150), (300, 160)), BOX, CTX)
    assert not fixed.is_polyline and clear(fixed)


def test_only_the_offending_span_is_replaced():
    pts = [(0, 0), (40, 0), (40, 150), (260, 150), (260, 300), (400, 300), (400, 20)]
    line = Route.from_points(pts, router="ortho")
    fixed = reroute.repair(line, BOX, CTX)
    a = fixed.anchors()
    assert clear(fixed) and is_ortho(fixed)
    assert a[:2] == pts[:2] and a[-3:] == pts[-3:]              # far ends untouched, manual shape kept


def test_each_leg_is_repaired_with_the_method_it_was_drawn_with():
    leg1 = drawn("straight", (0, 40), (150, 60))                # clear of the box
    leg2 = drawn("ortho", (150, 60), (150, 300), flip=False)    # vertical run through the box
    line = leg1.joined(leg2)
    assert [l["router"] for l in line.legs()] == ["straight", "ortho"]
    assert reroute.leg_at(line, 1)["router"] == "straight" and reroute.leg_at(line, 2)["router"] == "ortho"
    fixed = reroute.repair(line, BOX, CTX)
    assert clear(fixed)
    assert fixed.anchors()[:2] == [(0, 40), (150, 60)]          # the straight leg is untouched
    tail = Route.from_points(fixed.anchors()[1:])
    assert is_ortho(tail)                                       # the ortho leg is still orthogonal


def test_custom_lookup_and_unresolved_flag():
    seen = []
    def lookup(name):
        seen.append(name)
        return sl.create("avoid")
    fixed = reroute.repair(drawn("ortho", (0, 150), (300, 150)), BOX, CTX, lookup=lookup)
    assert seen == ["ortho"] and clear(fixed)
    stuck = reroute.repair(Route.from_points([(150, 150), (400, 150)], router="ortho"), BOX, CTX)
    assert stuck.meta.get("unresolved") is True                 # an end sits inside the shape


def test_avoiding_variants():
    assert isinstance(sl.create("ortho").avoiding(), sl.HugRouter)
    for name in ("hug", "avoid", "bus"):
        r = sl.create(name)
        assert r.avoiding() is r
    c = sl.create("cubic").avoiding()
    assert isinstance(c, sl.CubicRouter) and isinstance(c.base, sl.HugRouter) and c.name == "cubic"


# ------------------------------------------------ lines must not end up on top of each other

def _repair_all(lines, rect, obstacles, spacing=10.0, clearance=8.0):
    latest = list(lines)
    for k, line in enumerate(lines):
        others = [r.flatten() for m, r in enumerate(latest) if m != k]
        ctx = RouteContext(obstacles=obstacles, clearance=clearance, wire_spacing=spacing,
                           guides=others, bus_pitch=12)
        fixed = reroute.repair(line, rect, ctx, others=others)
        if fixed is not None:
            latest[k] = fixed
    return latest


def _shared_track(routes, tol=4.0):
    flats = [r.flatten() for r in routes]
    return max(g.overlap_length(flats[i], flats[j], tol)
               for i in range(len(flats)) for j in range(len(flats)) if i != j)


def test_overlap_length_counts_shared_track_not_crossings():
    assert g.overlap_length([(0, 0), (100, 0)], [(20, 2), (60, 2)], 5) == 40
    assert g.overlap_length([(0, 0), (100, 0)], [(50, -50), (50, 50)], 5) == 0      # a crossing
    assert g.overlap_length([(0, 0), (100, 0)], [(0, 12), (100, 12)], 5) == 0       # a bus neighbour


def test_bundle_of_lines_each_gets_its_own_lane():
    rect = (200, 100, 300, 260)
    for mode in ("ortho", "hug", "avoid", "straight", "octilinear"):
        lines = [drawn(mode, (0, 150 + 12 * k), (500, 150 + 12 * k)) for k in range(4)]
        fixed = _repair_all(lines, rect, [rect])
        assert all(clear(r, rect) for r in fixed), mode
        assert _shared_track(fixed) < 1e-6, (mode, [r.to_svg(0) for r in fixed])
        assert not any(r.meta.get("overlaps") for r in fixed), mode


def test_mixed_methods_keep_their_own_style_while_separating():
    rect = (200, 100, 300, 260)
    lines = [drawn("ortho", (0, 170), (500, 170)), drawn("cubic", (0, 182), (500, 182)),
             drawn("straight", (0, 140), (500, 215))]
    fixed = _repair_all(lines, rect, [rect])
    assert all(clear(r, rect) for r in fixed) and _shared_track(fixed) < 1e-6
    assert is_ortho(fixed[0]) and not fixed[1].is_polyline and not is_ortho(fixed[2])


def test_bus_members_stay_a_bus_around_the_shape():
    rect = (200, 100, 300, 260)
    first = drawn("ortho", (0, 170), (500, 170))
    second = sl.create("bus").shape((0, 182), (500, 182), RouteContext(guides=[first.flatten()], bus_pitch=12))
    second.meta.update(router="bus", flip=False, posture=None)
    fixed = _repair_all([first, second], rect, [rect])
    assert all(clear(r, rect) for r in fixed) and _shared_track(fixed) < 1e-6


def test_neighbouring_shape_does_not_force_lines_together():
    rect, neighbour = (200, 100, 300, 260), (330, 60, 420, 300)
    lines = [drawn("ortho", (0, 160 + 12 * k), (600, 160 + 12 * k)) for k in range(3)]
    fixed = _repair_all(lines, rect, [rect, neighbour])
    assert all(clear(r, rect) for r in fixed) and _shared_track(fixed) < 1e-6


# ------------------------------------------------ changed settings must reach existing lines

def _hug_line(clearance):
    return drawn("hug", (0, 150), (300, 150), obstacles=[BOX], clearance=clearance)


def test_repair_alone_ignores_a_line_that_is_already_clear():
    assert reroute.repair(_hug_line(10), BOX, RouteContext(obstacles=[BOX], clearance=30)) is None


def test_refresh_applies_new_clearance_both_ways():
    wide = reroute.refresh(_hug_line(10), RouteContext(obstacles=[BOX], clearance=30))
    assert {p[1] for p in wide.anchors()} >= {150.0, 70.0}            # 100 - 30
    tight = reroute.refresh(wide, RouteContext(obstacles=[BOX], clearance=4))
    assert {p[1] for p in tight.anchors()} >= {150.0, 96.0}
    assert tight.start == (0, 150) and tight.end == (300, 150) and tight.legs()[0]["router"] == "hug"


def test_refresh_applies_new_wire_spacing():
    a = drawn("hug", (0, 130), (300, 130), obstacles=[BOX], clearance=10)
    b = drawn("hug", (0, 140), (300, 140), obstacles=[BOX], clearance=10)
    for spacing, lane_y in ((10, 80.0), (25, 65.0)):
        ctx = RouteContext(obstacles=[BOX], clearance=10, wire_spacing=spacing)
        fixed_a = reroute.refresh(a, ctx, others=[b.flatten()])
        fixed_b = reroute.refresh(b, ctx, others=[fixed_a.flatten()])
        ys = {p[1] for p in fixed_a.anchors()} | {p[1] for p in fixed_b.anchors()}
        assert {90.0, lane_y} <= ys, (spacing, fixed_a.to_svg(0), fixed_b.to_svg(0))
        assert g.overlap_length(fixed_a.flatten(), fixed_b.flatten(), 4) == 0      # run-ins are pinned 10 px apart


def test_refresh_applies_new_bend_penalty_and_bus_pitch():
    boxes = [(100, 0, 140, 400), (200, 100, 240, 600)]
    line = drawn("avoid", (0, 450), (340, 50), obstacles=boxes, clearance=10, bend_penalty=0)
    cheap = reroute.refresh(line, RouteContext(obstacles=boxes, clearance=10, bend_penalty=0))
    dear = reroute.refresh(line, RouteContext(obstacles=boxes, clearance=10, bend_penalty=2000))
    assert g.bends(dear.flatten()) <= g.bends(cheap.flatten())
    guide = [(0, 300), (400, 300)]
    member = sl.create("bus").shape((0, 312), (400, 312), RouteContext(guides=[guide], bus_pitch=12))
    member.meta.update(router="bus", flip=False, posture=None)
    moved = reroute.refresh(Route.from_points([(0, 312), (400, 312)], router="bus"),
                            RouteContext(guides=[guide], bus_pitch=20, bus_capture=60), others=[guide])
    assert any(abs(p[1] - 320) < 1e-6 for p in moved.anchors())        # lane is now 20 px out


def test_refresh_keeps_leg_boundaries_and_methods():
    line = drawn("straight", (0, 40), (150, 60)).joined(drawn("ortho", (150, 60), (150, 300)))
    out = reroute.refresh(line, CTX)
    assert (150, 60) in out.anchors() and [l["router"] for l in out.legs()] == ["straight", "ortho"]
    assert clear(out) and out.end == (150, 300)
