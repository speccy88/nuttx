import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

from i2c_protocol import (
    BMP180_MARKER,
    BUS_MARKER,
    ID_MARKER,
    START_MARKER,
    marker_patterns,
    parse_i2c,
)


class I2cProtocolTests(unittest.TestCase):
    def good_output(self):
        return "\r\n".join(
            (
                "P2I2C:BUS_RECOVERY=PASS:SDA=24:SCL=25:PULSES=0",
                BUS_MARKER,
                BMP180_MARKER,
                START_MARKER,
                ID_MARKER,
                "P2I2C:READINGS=32:MIN=100930:MAX=101144:FNV1A=8A79CB15",
                "P2I2C:PASS",
            )
        ) + "\r\n"

    def test_complete_protocol_passes(self):
        result = parse_i2c(self.good_output())
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["reset_count"], 1)
        self.assertEqual(result["values"]["reading_count"], 32)
        self.assertEqual(result["values"]["minimum_pa"], 100930)
        self.assertEqual(result["values"]["maximum_pa"], 101144)
        self.assertEqual(result["values"]["recovery_pulses"], 0)

    def test_id_and_repeated_start_marker_are_exact(self):
        result = parse_i2c(self.good_output().replace("ID=0x55", "ID=0x56"))
        self.assertFalse(result["complete"])
        self.assertIn("missing {}".format(ID_MARKER), result["errors"])

        result = parse_i2c(
            self.good_output().replace(
                "TRANSFER=WRITE_RESTART_READ", "TRANSFER=WRITE_STOP_READ"
            )
        )
        self.assertFalse(result["complete"])
        self.assertIn("missing {}".format(ID_MARKER), result["errors"])

    def test_reading_count_and_pressure_bounds_are_enforced(self):
        result = parse_i2c(
            self.good_output().replace(
                "READINGS=32:MIN=100930:MAX=101144",
                "READINGS=31:MIN=29999:MAX=120001",
            )
        )
        self.assertFalse(result["complete"])
        self.assertTrue(any("count is 31" in item for item in result["errors"]))
        self.assertTrue(any("minimum pressure" in item for item in result["errors"]))
        self.assertTrue(any("maximum pressure" in item for item in result["errors"]))

    def test_hash_is_exact_uppercase_hex(self):
        result = parse_i2c(self.good_output().replace("8A79CB15", "8a79cb15"))
        self.assertFalse(result["complete"])
        self.assertIn("missing P2I2C:READINGS record", result["errors"])

    def test_failure_cannot_be_hidden_by_pass(self):
        result = parse_i2c(
            self.good_output() + "P2I2C:FAIL:pressure-read:5\r\n"
        )
        self.assertFalse(result["complete"])
        self.assertEqual(result["failures"][0]["kind"], "P2I2C failure")

    def test_duplicate_reset_and_marker_order_fail(self):
        duplicate = parse_i2c(
            self.good_output()
            + "P2I2C:BUS_RECOVERY=PASS:SDA=24:SCL=25:PULSES=1\r\n"
        )
        self.assertFalse(duplicate["complete"])
        self.assertEqual(duplicate["reset_count"], 2)

        lines = self.good_output().splitlines()
        lines[5], lines[6] = lines[6], lines[5]
        out_of_order = parse_i2c("\r\n".join(lines) + "\r\n")
        self.assertFalse(out_of_order["complete"])
        self.assertIn("protocol markers are out of order", out_of_order["errors"])

    def test_board_init_markers_are_exact_and_recovery_is_bounded(self):
        missing_bus = parse_i2c(self.good_output().replace(BUS_MARKER + "\r\n", ""))
        self.assertFalse(missing_bus["complete"])
        self.assertIn("missing {}".format(BUS_MARKER), missing_bus["errors"])

        invalid_pulses = parse_i2c(self.good_output().replace("PULSES=0", "PULSES=10"))
        self.assertFalse(invalid_pulses["complete"])
        self.assertIn(
            "missing P2I2C:BUS_RECOVERY record", invalid_pulses["errors"]
        )

    def test_streaming_markers_are_strict_and_ordered(self):
        markers = marker_patterns()
        labels = [label for label, _pattern in markers]
        self.assertEqual(
            labels,
            [
                "P2I2C:BUS_RECOVERY=PASS",
                BUS_MARKER,
                BMP180_MARKER,
                START_MARKER,
                ID_MARKER,
                "P2I2C:READINGS=32",
                "P2I2C:PASS",
            ],
        )
        text = self.good_output()
        for _label, pattern in markers:
            self.assertIsNotNone(pattern.search(text))


if __name__ == "__main__":
    unittest.main()
