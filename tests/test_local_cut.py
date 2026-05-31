import pytest

from aedt_agent.layout.local_cut import bbox_to_polygon, parse_local_cut_region


def test_parse_local_cut_region_accepts_bbox_and_preserves_unit():
    region = parse_local_cut_region(
        {
            "type": "bbox",
            "unit": "mil",
            "x_min": 5400.0,
            "y_min": 1100.0,
            "x_max": 6200.0,
            "y_max": 1500.0,
        }
    )

    assert region == {
        "type": "bbox",
        "unit": "mil",
        "x_min": 5400.0,
        "y_min": 1100.0,
        "x_max": 6200.0,
        "y_max": 1500.0,
    }


def test_parse_local_cut_region_rejects_missing_unit():
    with pytest.raises(ValueError, match="unit"):
        parse_local_cut_region({"type": "bbox", "x_min": 0, "y_min": 0, "x_max": 1, "y_max": 1})


def test_parse_local_cut_region_rejects_inverted_bbox():
    with pytest.raises(ValueError, match="x_min must be less than x_max"):
        parse_local_cut_region({"type": "bbox", "unit": "mil", "x_min": 10, "y_min": 0, "x_max": 1, "y_max": 1})


def test_bbox_to_polygon_returns_closed_clockwise_points():
    region = parse_local_cut_region({"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4})

    polygon = bbox_to_polygon(region)

    assert polygon == {
        "type": "polygon",
        "unit": "mil",
        "points": [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0], [1.0, 2.0]],
    }
