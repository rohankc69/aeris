from itertools import pairwise

from shapely.geometry import LineString, box

from aeris.planning.routing import crosses, detour

POCKET = box(400, 400, 600, 600)


def test_direct_leg_needs_no_detour() -> None:
    assert detour((0, 0), (300, 300), [POCKET]) == []
    assert not crosses((0, 0), (300, 300), [POCKET])


def test_detour_clears_a_single_pocket() -> None:
    via = detour((0, 500), (1000, 500), [POCKET])
    assert via, "leg through the pocket must be rerouted"
    path = [(0, 500), *via, (1000, 500)]
    for a, b in pairwise(path):
        assert not LineString([a, b]).intersects(POCKET)
    assert len(via) <= 2


def test_detour_is_deterministic_and_short() -> None:
    a = detour((0, 500), (1000, 500), [POCKET])
    b = detour((0, 500), (1000, 500), [POCKET])
    assert a == b
    total = LineString([(0, 500), *a, (1000, 500)]).length
    assert total < 1000 * 1.4


def test_diagonal_crossing_uses_two_corners_if_needed() -> None:
    via = detour((300, 300), (700, 700), [POCKET])
    path = [(300, 300), *via, (700, 700)]
    assert all(not LineString([p, q]).intersects(POCKET) for p, q in pairwise(path))
