import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import feedline  # noqa: E402

EXAMPLE = ROOT / "examples" / "backyard.ant"


class DipoleMathTests(unittest.TestCase):
    def test_full_size_dipole_at_20m_is_about_10_metres(self):
        # 142.5 / 14.175 MHz ≈ 10.05 m with the 0.95 end factor at VF 1.0
        self.assertAlmostEqual(feedline.dipole_total_m(14.175), 142.5 / 14.175, places=6)

    def test_velocity_factor_scales_length_linearly(self):
        full = feedline.dipole_total_m(7.1, 1.0)
        self.assertAlmostEqual(feedline.dipole_total_m(7.1, 0.95), full * 0.95, places=9)

    def test_quarter_wave_vertical_is_half_the_dipole_leg_length_scale(self):
        # A quarter-wave vertical is half the total length of a half-wave dipole.
        self.assertAlmostEqual(feedline.vertical_m(7.1) * 2, feedline.dipole_total_m(7.1), places=9)

    def test_velocity_factor_round_trips_from_measurement(self):
        length = feedline.dipole_total_m(14.2, 0.93)
        self.assertAlmostEqual(feedline.vf_from_dipole(length, 14.2), 0.93, places=9)


class SwrTests(unittest.TestCase):
    def test_swr_of_two_gives_known_reflection_and_losses(self):
        self.assertAlmostEqual(feedline.gamma_from_swr(2.0), 1 / 3, places=12)
        self.assertAlmostEqual(feedline.return_loss_db(2.0), 9.5424, places=3)
        self.assertAlmostEqual(feedline.mismatch_loss_db(2.0), 0.5115, places=3)

    def test_perfect_match_has_infinite_return_loss_and_no_mismatch_loss(self):
        self.assertEqual(feedline.return_loss_db(1.0), math.inf)
        self.assertEqual(feedline.mismatch_loss_db(1.0), 0.0)

    def test_return_loss_and_gamma_round_trip_back_to_swr(self):
        swr = feedline.swr_from_return_loss(feedline.return_loss_db(1.7))
        self.assertAlmostEqual(swr, 1.7, places=9)

    def test_swr_below_one_is_rejected(self):
        with self.assertRaises(ValueError):
            feedline.gamma_from_swr(0.9)


class FeedlineLossTests(unittest.TestCase):
    def test_loss_scales_with_length_and_with_sqrt_frequency(self):
        at_100 = feedline.feedline_loss_db("RG-58", 100.0, 100.0)
        self.assertAlmostEqual(at_100, 6.5, places=9)  # 0.65 * sqrt(100) per 100 ft
        self.assertAlmostEqual(feedline.feedline_loss_db("RG-58", 100.0, 200.0), 13.0, places=9)
        self.assertAlmostEqual(feedline.feedline_loss_db("RG-58", 400.0, 100.0), 13.0, places=9)

    def test_unknown_cable_is_an_error(self):
        with self.assertRaises(ValueError):
            feedline.feedline_loss_db("RG-999", 14.0, 50.0)

    def test_delivered_power_drops_with_loss_and_mismatch(self):
        matched = feedline.delivered_percent(0.0, 1.0)
        self.assertAlmostEqual(matched, 100.0, places=9)
        self.assertLess(feedline.delivered_percent(3.0, 1.0), 51.0)


class MatchingTests(unittest.TestCase):
    def test_quarter_wave_transformer_impedance_is_geometric_mean(self):
        self.assertAlmostEqual(feedline.transformer_z0(25.0, 50.0), math.sqrt(1250.0), places=9)

    def test_quarter_wave_length_uses_velocity_factor(self):
        self.assertAlmostEqual(feedline.quarter_wave_m(100.0, 0.66), 0.495, places=9)


class StationParsingTests(unittest.TestCase):
    def test_example_file_parses_cleanly(self):
        st, errors = feedline.parse_station(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(errors, [])
        self.assertEqual(st.cable, "LMR-400")
        self.assertEqual([b.name for b in st.bands], ["40m", "20m", "15m", "10m", "6m"])
        self.assertEqual(st.bands[-1].freq, feedline.BANDS["6m"])

    def test_errors_carry_line_numbers(self):
        text = "vf = 1.4\ncable = RG-999\nband 40m swr=0.5\nband 99x\nbogus line here\n"
        st, errors = feedline.parse_station(text)
        lines = sorted(line for line, _ in errors)
        self.assertEqual(lines, [1, 2, 3, 4, 5])

    def test_repeated_settings_and_bands_are_flagged(self):
        text = "vf = 0.9\nvf = 0.8\nband 40m\nband 40m\n"
        _, errors = feedline.parse_station(text)
        messages = [msg for _, msg in errors]
        self.assertTrue(any("repeated" in m for m in messages))
        self.assertEqual(len(errors), 2)

    def test_station_without_bands_is_reported(self):
        _, errors = feedline.parse_station("name = empty\n")
        self.assertEqual(errors, [(None, "no band lines found")])


class EvaluationTests(unittest.TestCase):
    def test_example_station_fails_only_the_10m_band(self):
        st, _ = feedline.parse_station(EXAMPLE.read_text(encoding="utf-8"))
        rows = {r["band"]: r for r in feedline.evaluate(st)}
        self.assertEqual(rows["10m"]["status"], "fail")
        self.assertIn("swr 2.6 > 2", rows["10m"]["problems"][0])
        self.assertEqual(rows["40m"]["status"], "ok")
        self.assertEqual(rows["6m"]["status"], "ok")

    def test_loss_limit_applies_to_feed_plus_mismatch(self):
        text = "cable = RG-58\nrun_ft = 100\nloss_limit_db = 1\nband 10m swr=1\n"
        st, errors = feedline.parse_station(text)
        self.assertEqual(errors, [])
        row = feedline.evaluate(st)[0]
        self.assertEqual(row["status"], "fail")
        self.assertTrue(any(p.startswith("loss") for p in row["problems"]))


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "feedline.py"), *args],
            capture_output=True, text=True, check=False)

    def test_check_gate_returns_one_for_example_failure(self):
        result = self.run_cli("check", str(EXAMPLE))
        self.assertEqual(result.returncode, 1)
        self.assertIn("10m FAIL", result.stdout)
        self.assertIn("4/5 bands pass", result.stdout)

    def test_plan_json_is_valid_and_has_every_band(self):
        result = self.run_cli("plan", str(EXAMPLE), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads(result.stdout)
        self.assertEqual(len(doc["bands"]), 5)
        self.assertIsNone(doc["bands"][-1]["swr"])

    def test_lint_reports_file_and_line_for_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.ant"
            bad.write_text("vf = 9\n", encoding="utf-8")
            result = self.run_cli("lint", str(bad))
            self.assertEqual(result.returncode, 2)
            self.assertIn("bad.ant:1:", result.stderr)

    def test_zero_frequency_is_a_usage_error_not_a_traceback(self):
        result = self.run_cli("dipole", "0")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_swr_command_prints_reflection(self):
        result = self.run_cli("swr", "--swr", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0.5000", result.stdout)


if __name__ == "__main__":
    unittest.main()
