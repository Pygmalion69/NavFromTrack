import unittest

from gpx_osm_to_directions import (
    Run,
    assert_valid_polyline,
    build_route_json,
    decode_polyline,
    encode_polyline,
    validate_route_options_consistency,
    validate_route_polylines,
)


class PolylineValidationTests(unittest.TestCase):
    def build_route(self, points, runs):
        route, candidates = build_route_json(
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
        return route, candidates

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
        route, _ = self.build_route(points, runs)
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
        route, _ = self.build_route(points, runs)
        validate_route_polylines(route, geometries="polyline6")

    def test_route_options_and_step_instructions_present(self):
        points = [
            (51.7879486, 6.1436697),
            (51.7879511, 6.1438125),
            (51.7881000, 6.1440000),
        ]
        runs = [
            Run(start_idx=0, end_idx=1, way_id=1, name="A", highway="residential", tags={"highway": "residential"}),
            Run(start_idx=1, end_idx=2, way_id=2, name="B", highway="residential", tags={"highway": "residential"}),
        ]
        route, _ = self.build_route(points, runs)

        validate_route_options_consistency(route, profile="car", geometries="polyline6")
        route_options = route["routeOptions"]
        self.assertIs(route_options.get("voiceInstructions"), True)
        self.assertIs(route_options.get("bannerInstructions"), True)
        self.assertIs(route_options.get("roundaboutExits"), True)

        for step in route["legs"][0]["steps"]:
            maneuver_type = step.get("maneuver", {}).get("type")
            if maneuver_type == "arrive":
                self.assertIsInstance(step.get("voiceInstructions"), list)
                self.assertIsInstance(step.get("bannerInstructions"), list)
                continue
            self.assertIsInstance(step.get("voiceInstructions"), list)
            self.assertGreaterEqual(len(step["voiceInstructions"]), 1)
            self.assertIsInstance(step.get("bannerInstructions"), list)
            self.assertGreaterEqual(len(step["bannerInstructions"]), 1)

    def test_malformed_polyline_rejected(self):
        with self.assertRaises(ValueError):
            assert_valid_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq", precision=5, label="broken polyline")

    def test_reversed_turn_regression_uses_gpx_headings(self):
        points = [
            (0.0, 0.0),
            (0.0, 0.0002),
            (0.0, 0.0004),
            (-0.00002, 0.00041),
            (0.0002, 0.00041),
            (0.0004, 0.00041),
        ]
        runs = [
            Run(start_idx=0, end_idx=2, way_id=1, name="First Rd", highway="residential", tags={"highway": "residential"}),
            Run(start_idx=2, end_idx=5, way_id=2, name="Second Rd", highway="residential", tags={"highway": "residential"}),
        ]

        route, candidates = self.build_route(points, runs)

        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].emit)
        self.assertLess(candidates[0].signed_delta, 0.0)
        turn_step = route["legs"][0]["steps"][1]
        self.assertEqual(turn_step["maneuver"]["type"], "turn")
        self.assertEqual(turn_step["maneuver"]["modifier"], "left")
        self.assertIn("onto Second Rd", turn_step["maneuver"]["instruction"])

    def test_repeated_continue_boundaries_are_suppressed(self):
        points = [
            (0.0, 0.0),
            (0.0, 0.0002),
            (0.0, 0.0004),
            (0.0, 0.0006),
            (0.0, 0.0008),
            (0.0, 0.0010),
        ]
        runs = [
            Run(start_idx=0, end_idx=2, way_id=1, name="Alpha", highway="residential", tags={"highway": "residential"}),
            Run(start_idx=2, end_idx=4, way_id=2, name="Bravo", highway="residential", tags={"highway": "residential"}),
            Run(start_idx=4, end_idx=5, way_id=3, name="Charlie", highway="residential", tags={"highway": "residential"}),
        ]

        route, candidates = self.build_route(points, runs)

        self.assertTrue(all(not candidate.emit for candidate in candidates))
        self.assertEqual([step["maneuver"]["type"] for step in route["legs"][0]["steps"]], ["depart", "arrive"])

    def test_roundabout_is_single_composite_maneuver(self):
        points = [
            (0.0, 0.0),
            (0.0, 0.0002),
            (0.0, 0.0004),
            (0.0001, 0.0005),
            (0.0002, 0.0004),
            (0.0002, 0.0002),
            (0.0002, 0.0),
            (0.0002, -0.0002),
        ]
        runs = [
            Run(start_idx=0, end_idx=2, way_id=1, name="Entry Rd", highway="primary", tags={"highway": "primary"}),
            Run(start_idx=2, end_idx=4, way_id=10, name="", highway="primary", tags={"highway": "primary", "junction": "roundabout"}),
            Run(start_idx=4, end_idx=5, way_id=11, name="", highway="primary", tags={"highway": "primary", "junction": "roundabout"}),
            Run(start_idx=5, end_idx=7, way_id=2, name="Exit Rd", highway="secondary", tags={"highway": "secondary"}),
        ]

        route, candidates = self.build_route(points, runs)

        roundabout_candidates = [candidate for candidate in candidates if candidate.maneuver_type == "roundabout"]
        self.assertEqual(len(roundabout_candidates), 1)
        self.assertTrue(roundabout_candidates[0].emit)
        self.assertEqual(roundabout_candidates[0].roundabout_exit, 2)

        steps = route["legs"][0]["steps"]
        self.assertEqual([step["maneuver"]["type"] for step in steps], ["depart", "roundabout", "arrive"])
        self.assertEqual(steps[1]["maneuver"]["exit"], 2)
        self.assertIn("2nd exit onto Exit Rd", steps[1]["maneuver"]["instruction"])

    def test_same_road_bend_is_suppressed(self):
        points = [
            (0.0, 0.0),
            (0.0, 0.0002),
            (0.0, 0.0004),
            (0.00008, 0.00058),
            (0.00016, 0.00076),
        ]
        runs = [
            Run(start_idx=0, end_idx=2, way_id=1, name="Main St", highway="residential", tags={"highway": "residential"}),
            Run(start_idx=2, end_idx=4, way_id=2, name="Main St", highway="residential", tags={"highway": "residential"}),
        ]

        route, candidates = self.build_route(points, runs)

        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0].emit)
        self.assertIn("same-road", candidates[0].reason)
        self.assertEqual([step["maneuver"]["type"] for step in route["legs"][0]["steps"]], ["depart", "arrive"])


if __name__ == "__main__":
    unittest.main()
