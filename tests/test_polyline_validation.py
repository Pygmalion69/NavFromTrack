import unittest

from gpx_osm_to_directions import (
    Run,
    assert_valid_polyline,
    build_route_json,
    decode_polyline,
    encode_polyline,
    validate_route_polylines,
)


class PolylineValidationTests(unittest.TestCase):
    def test_encode_decode_roundtrip_precision_5_and_6(self):
        coords_latlon = [
            (51.7879486, 6.1436697),
            (51.7879511, 6.1438125),
            (51.7881000, 6.1440000),
        ]
        lonlat = [(lon, lat) for lat, lon in coords_latlon]

        for precision in (5, 6):
            encoded = encode_polyline(lonlat, precision=precision)
            decoded = decode_polyline(encoded, precision=precision)
            self.assertEqual(len(decoded), len(coords_latlon))
            tol = (0.5 / (10 ** precision)) + 1e-12
            for (exp_lat, exp_lon), (got_lat, got_lon) in zip(coords_latlon, decoded):
                self.assertLessEqual(abs(got_lat - exp_lat), tol)
                self.assertLessEqual(abs(got_lon - exp_lon), tol)

    def test_route_geometry_validation_passes_for_generated_route(self):
        points = [
            (51.7879486, 6.1436697),
            (51.7879511, 6.1438125),
            (51.7881000, 6.1440000),
        ]
        runs = [
            Run(start_idx=0, end_idx=1, way_id=1, name="A", highway="residential", tags={"highway": "residential"}),
            Run(start_idx=1, end_idx=2, way_id=2, name="B", highway="residential", tags={"highway": "residential"}),
        ]
        route, _ = build_route_json(
            points=points,
            runs=runs,
            profile="car",
            locale="en",
            geometries="polyline6",
            speed_kmh=30.0,
            continue_threshold_deg=15.0,
            turn_threshold_deg=30.0,
            merge_below_m=0.0,
        )
        assert_valid_polyline(route["geometry"], precision=6, label="route geometry")

    def test_step_geometry_validation_passes_for_generated_route(self):
        points = [
            (51.7879486, 6.1436697),
            (51.7879511, 6.1438125),
            (51.7881000, 6.1440000),
        ]
        runs = [
            Run(start_idx=0, end_idx=1, way_id=1, name="A", highway="residential", tags={"highway": "residential"}),
            Run(start_idx=1, end_idx=2, way_id=2, name="B", highway="residential", tags={"highway": "residential"}),
        ]
        route, _ = build_route_json(
            points=points,
            runs=runs,
            profile="car",
            locale="en",
            geometries="polyline6",
            speed_kmh=30.0,
            continue_threshold_deg=15.0,
            turn_threshold_deg=30.0,
            merge_below_m=0.0,
        )
        validate_route_polylines(route, geometries="polyline6")

    def test_malformed_polyline_rejected(self):
        with self.assertRaises(ValueError):
            assert_valid_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq", precision=5, label="broken polyline")


if __name__ == "__main__":
    unittest.main()
