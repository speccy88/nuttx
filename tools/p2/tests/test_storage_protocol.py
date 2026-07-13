import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import storage_protocol as storage


class StorageProtocolTests(unittest.TestCase):
    sequence = "1234ABCD"

    def response(self, action, body):
        lines = ["P2STORAGE:BEGIN:COMMAND={}".format(action)]
        lines.extend(body)
        lines.append("P2STORAGE:PASS:{}".format(action.upper()))
        return "\r\n".join(lines) + "\r\n"

    def test_sequence_is_an_exact_uppercase_32_bit_nonce(self):
        self.assertEqual(storage.normalize_sequence(0x1234ABCD), self.sequence)
        self.assertEqual(storage.normalize_sequence(self.sequence), self.sequence)
        for invalid in ("1234abcd", "0x1234ABCD", "1234ABC", "1234ABCDE", -1):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    storage.normalize_sequence(invalid)

    def test_record_bytes_and_checksum_match_target_byte_protocol(self):
        flash = storage.record_bytes("flash", self.sequence)
        sd = storage.record_bytes("sd", self.sequence)

        self.assertEqual(len(flash), 256)
        self.assertEqual(flash[:8], b"P2STRG1F")
        self.assertEqual(sd[:8], b"P2STRG1S")
        self.assertEqual(flash[8:12], b"\xcd\xab\x34\x12")
        self.assertEqual(storage.record_checksum("flash", self.sequence), "EAB1894E")
        self.assertEqual(storage.record_checksum("sd", self.sequence), "F0961869")
        self.assertEqual(
            int.from_bytes(flash[-4:], "little"), storage.fnv1a(flash[:252])
        )

    def test_commands_include_acknowledgement_only_for_destructive_actions(self):
        self.assertEqual(storage.command_line("probe"), "p2storage probe")
        self.assertEqual(
            storage.command_line("flash-write", self.sequence),
            "p2storage flash-write {} {}".format(
                storage.ACKNOWLEDGEMENT, self.sequence
            ),
        )
        self.assertEqual(
            storage.command_line("flash-verify", self.sequence),
            "p2storage flash-verify {}".format(self.sequence),
        )
        with self.assertRaisesRegex(ValueError, "requires"):
            storage.command_line("sd-write")
        with self.assertRaisesRegex(ValueError, "does not accept"):
            storage.command_line("probe", self.sequence)

    def test_flash_write_requires_predicted_checksum_nonce_and_reset_marker(self):
        checksum = storage.stream_checksum("flash", self.sequence)
        text = self.response(
            "flash-write",
            [
                "P2STORAGE:FLASH:WRITE:SEQUENCE={}:BYTES=1048576:"
                "FNV1A={}:PASS".format(
                    self.sequence, checksum
                ),
                "P2STORAGE:READY:RESET=FLASH:SEQUENCE={}".format(self.sequence),
            ],
        )

        result = storage.parse_storage_response(
            text, "flash-write", self.sequence
        )

        self.assertTrue(result["complete"], result)
        self.assertEqual(result["expected_checksum"], checksum)

        stale = storage.parse_storage_response(
            text, "flash-write", "1234ABCE"
        )
        self.assertFalse(stale["complete"])
        self.assertTrue(stale["missing"])

    def test_persistence_checksum_cannot_be_a_target_chosen_value(self):
        text = self.response(
            "sd-verify",
            [
                "P2STORAGE:SD:PERSISTENCE:SEQUENCE={}:BYTES=1048576:"
                "FNV1A=00000000:PASS".format(
                    self.sequence
                )
            ],
        )
        result = storage.parse_storage_response(text, "sd-verify", self.sequence)
        self.assertFalse(result["complete"])
        self.assertIn(
            storage.stream_checksum("sd", self.sequence), result["missing"][0]
        )

    def test_probe_requires_both_exact_device_paths_and_positive_geometry(self):
        text = self.response(
            "probe",
            [
                "P2STORAGE:PROBE:FLASH:DEV=/dev/smart0:AVAILABLE=1:WRITE=1:"
                "SECTORS=31744:SECTORSIZE=512:PASS",
                "P2STORAGE:PROBE:SD:DEV=/dev/mmcsd0:AVAILABLE=1:WRITE=1:"
                "SECTORS=62333952:SECTORSIZE=512:PASS",
            ],
        )
        result = storage.parse_storage_response(text, "probe")
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["captures"]["flash_sectorsize"], "512")

        wrong = text.replace("/dev/smart0", "/dev/mtdsmart0")
        self.assertFalse(storage.parse_storage_response(wrong, "probe")["complete"])

    def test_failure_duplicate_and_out_of_order_cannot_be_hidden_by_pass(self):
        format_line = "P2STORAGE:FLASH:FORMAT:PASS"
        text = self.response("flash-format", [format_line])
        failed = text.replace(
            format_line,
            "P2STORAGE:FAIL:MOUNT:5\r\n" + format_line,
        )
        self.assertFalse(
            storage.parse_storage_response(failed, "flash-format")["complete"]
        )

        duplicate = text.replace(format_line, format_line + "\r\n" + format_line)
        result = storage.parse_storage_response(duplicate, "flash-format")
        self.assertFalse(result["complete"])
        self.assertIn(format_line, result["duplicates"])

        out_of_order = self.response(
            "sd-rename-delete",
            [
                "P2STORAGE:SD:DELETE:SEQUENCE={}:PASS".format(self.sequence),
                "P2STORAGE:SD:RENAME:SEQUENCE={}:PASS".format(self.sequence),
            ],
        )
        result = storage.parse_storage_response(
            out_of_order, "sd-rename-delete", self.sequence
        )
        self.assertFalse(result["complete"])
        self.assertFalse(result["order_valid"])

    def test_alternate_requires_every_one_of_1000_ordered_transactions(self):
        markers = storage.response_marker_patterns("alternate", self.sequence)
        labels = [label for label, pattern in markers]
        iterations = [label for label in labels if ":ITERATION=" in label]
        self.assertEqual(len(iterations), storage.ALTERNATE_TRANSACTIONS)
        self.assertEqual(iterations[0], "P2STORAGE:BUS:ITERATION=1")
        self.assertEqual(iterations[-1], "P2STORAGE:BUS:ITERATION=1000")

    def test_flash_cycle_and_sd_stress_predict_every_wrapped_sequence_checksum(self):
        flash = storage.response_marker_patterns("flash-cycle", "FFFFFFF8")
        flash_labels = [label for label, pattern in flash]
        self.assertIn(
            "P2STORAGE:FLASH:CYCLE:ITERATION=9:SEQUENCE=00000000:"
            "FNV1A={}:PASS".format(storage.record_checksum("flash", "00000000")),
            flash_labels,
        )
        self.assertIn(
            "P2STORAGE:FLASH:CYCLE:COUNT=16:PASS", flash_labels
        )

        sd = storage.response_marker_patterns("sd-stress", "FFFFFFE0")
        sd_labels = [label for label, pattern in sd]
        self.assertIn(
            "P2STORAGE:SD:STRESS:ITERATION=33:SEQUENCE=00000000:"
            "FNV1A={}:PASS".format(storage.record_checksum("sd", "00000000")),
            sd_labels,
        )
        self.assertIn("P2STORAGE:SD:STRESS:COUNT=64:PASS", sd_labels)

    def test_flash_full_progress_preserves_strict_enospc_terminal(self):
        text = self.response(
            "flash-full",
            [
                "P2STORAGE:FLASH:FULL:PROGRESS:SEQUENCE={}:"
                "BYTES=1048576".format(self.sequence),
                "P2STORAGE:FLASH:FULL:PROGRESS:SEQUENCE={}:"
                "BYTES=2097152".format(self.sequence),
                "P2STORAGE:FLASH:FULL:SEQUENCE={}:BYTES=15532032:"
                "ENOSPC=1:PASS".format(self.sequence)
            ],
        )
        result = storage.parse_storage_response(text, "flash-full", self.sequence)
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["captures"]["flash_full_bytes"], "15532032")
        for replacement in ("BYTES=0", "ENOSPC=0", "SEQUENCE=1234ABCE"):
            with self.subTest(replacement=replacement):
                corrupted = text
                if replacement.startswith("BYTES"):
                    corrupted = corrupted.replace("BYTES=15532032", replacement)
                elif replacement.startswith("ENOSPC"):
                    corrupted = corrupted.replace("ENOSPC=1", replacement)
                else:
                    corrupted = corrupted.replace(
                        "SEQUENCE=1234ABCD", replacement
                    )
                self.assertFalse(
                    storage.parse_storage_response(
                        corrupted, "flash-full", self.sequence
                    )["complete"]
                )

    def test_interrupted_write_arm_ends_at_ready_and_recovery_is_strict(self):
        base = "FFFFFFFF"
        arm = self.response(
            "flash-interrupt-arm",
            [
                "P2STORAGE:FLASH:INTERRUPT:ARMED:BASE_SEQUENCE=FFFFFFFF:"
                "PENDING_SEQUENCE=00000000:WRITTEN=128",
                "P2STORAGE:READY:POWER-CUT=FLASH:SEQUENCE=FFFFFFFF",
            ],
        ).replace("P2STORAGE:PASS:FLASH-INTERRUPT-ARM\r\n", "")
        result = storage.parse_storage_response(
            arm, "flash-interrupt-arm", base
        )
        self.assertTrue(result["complete"], result)
        self.assertNotIn("P2STORAGE:PASS:FLASH-INTERRUPT-ARM", result["found"])
        self.assertIn(storage.ACKNOWLEDGEMENT, result["command"])

        verify = self.response(
            "flash-interrupt-verify",
            [
                "P2STORAGE:FLASH:INTERRUPT:PENDING=PREFIX:BYTES=128:PASS",
                "P2STORAGE:FLASH:INTERRUPT:RECOVERY:SEQUENCE=FFFFFFFF:PASS",
            ],
        )
        result = storage.parse_storage_response(
            verify, "flash-interrupt-verify", base
        )
        self.assertTrue(result["complete"], result)
        self.assertNotIn(storage.ACKNOWLEDGEMENT, result["command"])
        self.assertEqual(
            result["expected_checksum"], storage.stream_checksum("flash", base)
        )

        invalid = verify.replace("PREFIX:BYTES=128", "PREFIX:BYTES=129")
        result = storage.parse_storage_response(
            invalid, "flash-interrupt-verify", base
        )
        self.assertFalse(result["complete"])
        self.assertIn("0..128", result["errors"][0])

    def test_board_markers_pin_jedec_geometry_layout_and_no_autoformat(self):
        output = "".join(
            {
                "P2STORAGE:W25=PRIVATE JEDEC=EF7018":
                    "\nP2STORAGE:W25=PRIVATE JEDEC=EF7018\r\n",
                "P2STORAGE:W25_FREQUENCY PROBE=400000 ACTIVE=2000000":
                    "\nP2STORAGE:W25_FREQUENCY PROBE=400000 "
                    "ACTIVE=2000000\r\n",
                "P2STORAGE:W25_GEOMETRY":
                    "\nP2STORAGE:W25_GEOMETRY BLOCK=256 ERASE=4096 "
                    "ERASEBLOCKS=4096 BYTES=16777216\r\n",
                "P2STORAGE:W25_LAYOUT":
                    "\nP2STORAGE:W25_LAYOUT BOOT=0x00000000+0x00080000 "
                    "DATA=0x00080000+0x00F80000 FIRSTBLOCK=2048 "
                    "NBLOCKS=63488\r\n",
                "P2STORAGE:W25_BOOT_CRC32":
                    "\nP2STORAGE:W25_BOOT_CRC32=89ABCDEF\r\n",
                "P2STORAGE:SMARTFS=/dev/smart0 AUTOFORMAT=NO":
                    "\nP2STORAGE:SMARTFS=/dev/smart0 AUTOFORMAT=NO\r\n",
                "P2STORAGE:MMCSD_FREQUENCY ID=400000 TRANSFER=2000000":
                    "\nP2STORAGE:MMCSD_FREQUENCY ID=400000 "
                    "TRANSFER=2000000\r\n",
                "P2STORAGE:MMCSD=/dev/mmcsd0":
                    "\nP2STORAGE:MMCSD=/dev/mmcsd0\r\n",
            }[label]
            for label, pattern in storage.BOARD_MARKER_PATTERNS
        )
        for label, pattern in storage.BOARD_MARKER_PATTERNS:
            with self.subTest(label=label):
                self.assertIsNotNone(pattern.search(output))


if __name__ == "__main__":
    unittest.main()
