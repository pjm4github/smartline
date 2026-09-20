"""Route editing operations - no Qt required."""
import pytest

from smartline import Route, edit, geometry as g

Z = Route.from_points([(0, 0), (100, 0), (100, 80), (200, 80)])           # orthogonal "Z"
S = Route((0, 0)).cubic_to((50, 0), (50, 60), (100, 60)).cubic_to((150, 60), (150, 0), (200, 0))


def ortho(r):
    return edit.is_orthogonal(r)


def test_handles_inventory():
    kinds = [h.kind for h in edit.handles(Z)]
    assert kinds.count("anchor") == 4 and kinds.count("segment") == 3
    hs = edit.handles(S)
    assert [h.kind for h in hs[:4]] == ["c1", "c2", "c1", "c2"]            # controls listed first
    assert hs[1].anchor == (100, 60) and hs[2].anchor == (100, 60)         # tangent lines meet at the join
    assert not any(h.kind == "segment" for h in hs)


def test_drag_middle_section_of_orthogonal_route():
    r = edit.move_segment(Z, 1, (140, 33))
    assert r.anchors() == [(0, 0), (140, 0), (140, 80), (200, 80)] and ortho(r)


def test_drag_end_section_keeps_the_port_and_grows_a_jog():
    r = edit.move_segment(Z, 0, (50, -30))
    assert r.anchors() == [(0, 0), (0, -30), (100, -30), (100, 80), (200, 80)] and ortho(r)
    r = edit.move_segment(Z, 2, (150, 120))
    assert r.anchors()[-3:] == [(100, 120), (200, 120), (200, 80)] and ortho(r)


def test_drag_vertex_keeps_orthogonality():
    r = edit.move_anchor(Z, 0, (-20, 15))                     # an end: neighbour follows on its axis
    assert r.anchors()[:2] == [(-20, 15), (100, 15)] and ortho(r)
    r = edit.move_anchor(Z, 1, (130, 40))                     # next to a pinned end: slides along the run
    assert r.anchors() == [(0, 0), (130, 0), (130, 80), (200, 80)]
    free = edit.move_anchor(Z, 1, (130, 40), keep_orthogonal=False)
    assert free.anchors()[1] == (130, 40) and not ortho(free)


def test_single_run_grows_an_elbow():
    r = edit.move_anchor(Route.from_points([(0, 0), (100, 0)]), 1, (120, 50))
    assert r.anchors() == [(0, 0), (120, 0), (120, 50)]


def test_moving_a_curve_anchor_carries_its_tangent_handles():
    r = edit.move_anchor(S, 1, (110, 70))
    assert r.segs[0].pts == ((50, 0), (60, 70), (110, 70))
    assert r.segs[1].pts[0] == (160, 70) and r.segs[1].pts[1:] == S.segs[1].pts[1:]


def test_control_links_aligned_mirrored_free():
    al = edit.move_control(S, 0, "c2", (100, 20))             # handle now points straight up, 40 long
    assert al.segs[0].pts[1] == (100, 20)
    px, py = al.segs[1].pts[0]
    assert abs(px - 100) < 1e-9 and abs(py - 110) < 1e-9      # partner: opposite, keeps its length 50
    mi = edit.move_control(S, 0, "c2", (100, 20), link="mirrored")
    assert mi.segs[1].pts[0] == (100, 100)
    fr = edit.move_control(S, 0, "c2", (100, 20), link="free")
    assert fr.segs[1].pts[0] == S.segs[1].pts[0]


def test_insert_anchor_preserves_shape_and_delete_rejoins():
    r = edit.insert_anchor(S, 0, 0.4)
    assert len(r.segs) == 3
    for t in (0.1, 0.4, 0.75):
        from smartline.route import _bezier
        want = _bezier(((0, 0),) + S.segs[0].pts, t)
        assert edit.nearest_segment(r, want, samples=256)[2] < 0.01      # sampled distance
    back = edit.delete_anchor(r, 1)
    assert len(back.segs) == 2 and back.end == S.end
    assert edit.delete_anchor(Z, 1).anchors() == [(0, 0), (100, 80), (200, 80)]
    with pytest.raises(ValueError):
        edit.delete_anchor(Z, 0)


def test_convert_and_normalize():
    c = edit.convert_segment(Z, 1, "C")
    assert [s.cmd for s in c.segs] == ["L", "C", "L"] and c.segs[1].end == (100, 80)
    assert edit.convert_segment(c, 1, "L").anchors() == Z.anchors()
    jog = edit.move_segment(edit.move_segment(Z, 0, (0, -30)), 1, (0, 0))   # drag it back again
    assert edit.normalize(jog).anchors() == Z.anchors()


def test_nearest_segment():
    s, t, d = edit.nearest_segment(Z, (100, 40.5))
    assert s == 1 and abs(t - 0.50625) < 1e-9 and d < 1e-9
    assert edit.nearest_segment(S, (150, 30))[0] == 1


# ------------------------------------------------------------ trimming from an end
from smartline import blend_corners  # noqa: E402

ROUNDED = blend_corners([(0, 0), (100, 0), (100, 80), (200, 80)], 20)     # L C L C L


def test_end_span_takes_the_curve_attached_to_the_last_section():
    assert [s.cmd for s in ROUNDED.segs] == ["L", "C", "L", "C", "L"]
    assert edit.end_span(ROUNDED, "end") == [3, 4] and edit.end_span(ROUNDED, "start") == [0, 1]
    assert edit.end_span(Z, "end") == [2] and edit.end_span(S, "end") == [1]


def test_trim_end_eats_the_line_back_section_by_section():
    r = edit.trim_end(ROUNDED, "end")
    assert [s.cmd for s in r.segs] == ["L", "C", "L"] and r.start == (0, 0) and r.end == (100, 60)
    r = edit.trim_end(r, "end")
    assert [s.cmd for s in r.segs] == ["L"] and r.end == (80, 0)
    assert edit.trim_end(r, "end") is None                           # nothing would be left


def test_trim_start_and_leg_bookkeeping():
    r = edit.trim_end(ROUNDED, "start")
    assert r.start == (100, 20) and [s.cmd for s in r.segs] == ["L", "C", "L"] and r.end == (200, 80)
    two = Route.from_points([(0, 0), (50, 0)], router="ortho").joined(
        Route.from_points([(50, 0), (50, 40), (90, 40)], router="hug"))
    cut = edit.trim_end(two, "end")
    assert cut.end == (50, 40) and cut.legs()[-1]["end"] == (50, 40)
    assert edit.span_points(Z, [2]) == [(100, 80), (200, 80)]
