"""Un-kinking and bus-following - no Qt required."""
from smartline import Route, RouteContext, edit, geometry as g, reroute, tidy

BOX = (300, 60, 400, 200)


def bends(r):
    return g.bends(r.flatten())


def test_short_jog_is_collapsed_and_ends_stay_put():
    jog = Route.from_points([(0, 0), (100, 0), (100, 10), (250, 10), (250, 120), (400, 120)])
    out = tidy.unkink(jog)
    assert bends(jog) == 4 and bends(out) == 2
    assert out.start == jog.start and out.end == jog.end and edit.is_orthogonal(out)


def test_hairpin_spur_disappears():
    spur = Route.from_points([(0, 0), (200, 0), (200, 60), (203, 60), (203, 0), (400, 0), (400, 100)])
    out = tidy.unkink(spur)
    assert out.anchors() == [(0, 0), (400, 0), (400, 100)]


def test_long_sections_are_not_kinks():
    z = Route.from_points([(0, 0), (100, 0), (100, 80), (200, 80)])
    assert tidy.unkink(z) is None and tidy.kinks(z) == []
    assert tidy.unkink(Route((0, 0)).cubic_to((50, 0), (50, 60), (100, 60))) is None


def test_the_move_that_would_cut_into_a_shape_is_refused():
    ctx = RouteContext(obstacles=[BOX], clearance=10)
    # the 35 px step exists because of the box: flattening it would run the line through the shape
    line = Route.from_points([(0, 180), (280, 180), (280, 215), (600, 215), (600, 400)])
    assert tidy.unkink(line, ctx) is None
    assert bends(tidy.unkink(line)) == 1                          # without the shape it would go


def test_never_slides_a_run_onto_another_line():
    ctx = RouteContext(wire_spacing=10)
    line = Route.from_points([(0, 0), (100, 0), (100, 12), (300, 12), (300, 200)])
    blocker = [(-50, 12), (99, 12)]                              # a line already on y = 12, left part
    out = tidy.unkink(line, ctx, others=[blocker])
    assert bends(out) == 1 and out.anchors()[1] == (300, 0)      # collapsed upward, not onto y = 12


def test_messy_reroute_result_cleans_up_to_a_handful_of_bends():
    messy = Route.from_points([(130, 240), (270, 270), (540, 270), (540, 290), (555, 290), (555, 410),
                               (720, 410), (720, 460), (722, 460), (722, 405), (750, 405), (750, 395),
                               (930, 395), (930, 383), (932, 383), (932, 395), (960, 395), (960, 420),
                               (975, 420), (975, 590), (950, 590), (950, 600), (960, 600), (960, 575),
                               (1020, 605)])
    out = tidy.unkink(messy)
    assert bends(messy) >= 20 and bends(out) <= 7, (bends(out), out.to_svg(0))
    assert out.start == messy.start and out.end == messy.end


def test_free_angle_kink_is_shortcut():
    line = Route.from_points([(0, 0), (100, 40), (108, 35), (115, 44), (300, 120)])
    out = tidy.unkink(line)
    assert bends(out) < bends(line) and out.start == (0, 0) and out.end == (300, 120)


# ---------------------------------------------------------------- follow a bus

GUIDE = [(100, 100), (400, 100), (400, 300), (700, 300)]


def test_nearest_route_matches_by_whole_shape_not_by_one_end():
    mine = Route.from_points([(100, 130), (380, 130), (380, 330), (700, 330)])
    decoy = [(95, 128), (95, -400)]                              # touches my start, then leaves
    assert tidy.nearest_route(mine, [decoy, GUIDE]) == 1
    assert tidy.nearest_route(mine, []) is None


def test_follow_runs_parallel_at_the_pitch_between_its_own_ends():
    mine = Route.from_points([(100, 140), (250, 140), (250, 190), (360, 190), (360, 340), (700, 340)])
    out = tidy.follow(mine, GUIDE, RouteContext(bus_pitch=12))
    assert out.start == mine.start and out.end == mine.end
    a = out.anchors()
    assert (388, 112) in a and (388, 312) in a                   # the guide's corners, one pitch out
    assert out.legs()[0]["router"] == "bus"
    assert g.overlap_length(out.flatten(), GUIDE, 5) == 0


def test_follow_takes_the_next_lane_if_the_first_is_taken():
    member1 = g.offset_polyline(GUIDE, 12)
    mine = Route.from_points([(100, 150), (370, 150), (370, 350), (700, 350)])
    out = tidy.follow(mine, GUIDE, RouteContext(bus_pitch=12, wire_spacing=10), others=[GUIDE, member1])
    assert (376, 124) in out.anchors()                           # lane 2
    assert g.overlap_length(out.flatten(), member1, 5) == 0
