import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

from smartpins_protocol import (
    DIGITAL_PAYLOAD_FNV1A,
    hil_marker_patterns,
    parse_smartpins,
    stages_from_kconfig,
)


class SmartpinsProtocolTests(unittest.TestCase):
    def gpio_output(self):
        lines = [
            "P2SMART:BEGIN",
            "P2SMART:WIRING=P0-P1,P2-P3,P4-P5,P6-P7",
            "P2SMART:CAPS=GPIO",
            "P2SMART:GPIO:BEGIN=0-1",
        ]
        pattern = (0, 1, 1, 0, 1, 0, 0, 1)
        lines.extend(
            "P2SMART:GPIO:SAMPLE={}:TX={}:RX={}".format(index, value, value)
            for index, value in enumerate(pattern)
        )
        lines.extend(
            (
                "P2SMART:GPIO:SAFE=FLOAT",
                "P2SMART:GPIO:PASS",
                "P2SMART:PASS",
            )
        )
        return "\r\n".join(lines) + "\r\n"

    def full_output(self):
        text = self.gpio_output().replace(
            "P2SMART:CAPS=GPIO",
            "P2SMART:CAPS=GPIO,EDGE,UART,PWM_CAPTURE,DAC_ADC,SPI",
        ).replace("P2SMART:PASS\r\n", "", 1)
        lines = [
            "P2SMART:EDGE:BEGIN=0-1",
            "P2SMART:EDGE:COUNT=6",
            "P2SMART:EDGE:SAFE=FLOAT",
            "P2SMART:EDGE:PASS",
            "P2SMART:UART:BEGIN=2-3",
            "P2SMART:UART:COUNT=16:FNV1A={}".format(DIGITAL_PAYLOAD_FNV1A),
            "P2SMART:UART:SAFE=FLOAT",
            "P2SMART:UART:PASS",
            "P2SMART:PWM_CAPTURE:BEGIN=4-5",
            "P2SMART:PWM_CAPTURE:SAMPLE=0:FREQ=998:DUTY=24:EDGES=49",
            "P2SMART:PWM_CAPTURE:SAMPLE=1:FREQ=1002:DUTY=50:EDGES=50",
            "P2SMART:PWM_CAPTURE:SAMPLE=2:FREQ=1000:DUTY=76:EDGES=51",
            "P2SMART:PWM_CAPTURE:SAFE=FLOAT",
            "P2SMART:PWM_CAPTURE:PASS",
            "P2SMART:DAC_ADC:BEGIN=4-5",
            "P2SMART:DAC_ADC:SAMPLE=0:DAC=16383:ADC=4000",
            "P2SMART:DAC_ADC:SAMPLE=1:DAC=32767:ADC=8100",
            "P2SMART:DAC_ADC:SAMPLE=2:DAC=49151:ADC=12200",
            "P2SMART:DAC_ADC:SAFE=FLOAT",
            "P2SMART:DAC_ADC:PASS",
            "P2SMART:SPI:BEGIN=6-7",
            "P2SMART:SPI:COUNT=16:TX={0}:RX={0}".format(
                DIGITAL_PAYLOAD_FNV1A
            ),
            "P2SMART:SPI:SAFE=FLOAT",
            "P2SMART:SPI:PASS",
            "P2SMART:PASS",
        ]
        return text + "\r\n".join(lines) + "\r\n"

    def test_gpio_protocol_passes(self):
        result = parse_smartpins(self.gpio_output(), ("GPIO",))
        self.assertTrue(result["complete"])
        self.assertEqual(result["reset_count"], 1)
        self.assertEqual(result["stages"]["GPIO"]["sample_count"], 8)

    def test_full_protocol_passes(self):
        stages = ("GPIO", "EDGE", "UART", "PWM_CAPTURE", "DAC_ADC", "SPI")
        result = parse_smartpins(self.full_output(), stages)
        self.assertTrue(result["complete"], result)

    def test_gpio_data_mismatch_fails(self):
        text = self.gpio_output().replace(
            "P2SMART:GPIO:SAMPLE=4:TX=1:RX=1",
            "P2SMART:GPIO:SAMPLE=4:TX=1:RX=0",
        )
        result = parse_smartpins(text, ("GPIO",))
        self.assertFalse(result["complete"])
        self.assertIn("GPIO sample 4 data mismatch", result["errors"])

    def test_caps_must_match_exact_image_configuration(self):
        result = parse_smartpins(self.gpio_output(), ("GPIO", "UART"))
        self.assertFalse(result["complete"])
        self.assertTrue(any("does not match expected" in item for item in result["errors"]))

    def test_missing_safe_release_fails(self):
        result = parse_smartpins(
            self.gpio_output().replace("P2SMART:GPIO:SAFE=FLOAT\r\n", ""),
            ("GPIO",),
        )
        self.assertFalse(result["complete"])
        self.assertIn("missing P2SMART:GPIO:SAFE=FLOAT", result["errors"])

    def test_duplicate_begin_is_unexpected_reset(self):
        result = parse_smartpins(self.gpio_output() + "P2SMART:BEGIN\n", ("GPIO",))
        self.assertFalse(result["complete"])
        self.assertEqual(result["reset_count"], 2)

    def test_failure_line_cannot_be_hidden_by_pass(self):
        result = parse_smartpins(
            self.gpio_output() + "P2SMART:FAIL:GPIO:-5\n", ("GPIO",)
        )
        self.assertFalse(result["complete"])
        self.assertEqual(result["failures"][0]["kind"], "P2SMART failure")

    def test_pwm_tolerance_is_enforced(self):
        text = self.full_output().replace("FREQ=998", "FREQ=800")
        stages = ("GPIO", "EDGE", "UART", "PWM_CAPTURE", "DAC_ADC", "SPI")
        result = parse_smartpins(text, stages)
        self.assertFalse(result["complete"])
        self.assertTrue(any("outside 950..1050" in item for item in result["errors"]))

    def test_dac_adc_must_be_monotonic(self):
        text = self.full_output().replace("ADC=8100", "ADC=3900")
        stages = ("GPIO", "EDGE", "UART", "PWM_CAPTURE", "DAC_ADC", "SPI")
        result = parse_smartpins(text, stages)
        self.assertFalse(result["complete"])
        self.assertIn("DAC_ADC ADC samples are not strictly increasing", result["errors"])

    def test_exact_kconfig_derives_canonical_stages(self):
        config = {
            "CONFIG_TESTING_P2SMARTPINS": "y",
            "CONFIG_TESTING_P2SMARTPINS_UART": "y",
            "CONFIG_TESTING_P2SMARTPINS_PWM_CAPTURE": "y",
        }
        self.assertEqual(
            stages_from_kconfig(config), ("GPIO", "UART", "PWM_CAPTURE")
        )

    def test_streaming_markers_cover_every_gpio_sample(self):
        markers = hil_marker_patterns(("GPIO",))
        labels = [label for label, pattern in markers]
        self.assertEqual(
            [label for label in labels if "GPIO:SAMPLE" in label],
            ["P2SMART:GPIO:SAMPLE={}".format(index) for index in range(8)],
        )
        self.assertLess(
            labels.index("P2SMART:GPIO:SAMPLE=7"),
            labels.index("P2SMART:GPIO:SAFE=FLOAT"),
        )

    def test_streaming_markers_follow_pwm_sample_order(self):
        markers = hil_marker_patterns(("GPIO", "PWM_CAPTURE"))
        labels = [label for label, pattern in markers]
        self.assertLess(
            labels.index("P2SMART:PWM_CAPTURE:BEGIN=4-5"),
            labels.index("P2SMART:PWM_CAPTURE:SAMPLE=0"),
        )
        self.assertLess(
            labels.index("P2SMART:PWM_CAPTURE:SAMPLE=2"),
            labels.index("P2SMART:PWM_CAPTURE:SAFE=FLOAT"),
        )

    def test_streaming_markers_reject_noncanonical_or_analog_only_stages(self):
        with self.assertRaisesRegex(ValueError, "canonical"):
            hil_marker_patterns(("UART", "GPIO"))
        with self.assertRaisesRegex(ValueError, "include GPIO"):
            hil_marker_patterns(("DAC_ADC",))


if __name__ == "__main__":
    unittest.main()
