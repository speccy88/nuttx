#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Host unit tests for context/verify.py helpers."""

from __future__ import annotations

import importlib.util
import struct
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("p2_context_verify", HERE / "verify.py")
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class ContextVerifyTests(unittest.TestCase):
    def test_setint1_derivation(self) -> None:
        self.assertEqual(VERIFY.derive_setint1_immediate(0), 0xFD640025)
        self.assertEqual(VERIFY.derive_setint1_immediate(1), 0xFD640225)
        self.assertEqual(VERIFY.derive_setint1_immediate(0x1FF), 0xFD67FE25)

    def test_setint1_rejects_out_of_range_event(self) -> None:
        with self.assertRaises(ValueError):
            VERIFY.derive_setint1_immediate(0x200)

    def test_symbol_parser_ignores_null_undefined_entry(self) -> None:
        text = """
           0: 00000000     0 NOTYPE  LOCAL  DEFAULT   UND
           7: 00000a00    24 FUNC    GLOBAL DEFAULT     1 main
           8: 00000b00     0 NOTYPE  GLOBAL DEFAULT   UND real_missing
        """
        symbols, undefined = VERIFY.parse_symbols(text)
        self.assertEqual(symbols["main"], 0xA00)
        self.assertEqual(undefined, ["real_missing"])

    def test_first_load_paddr(self) -> None:
        text = "  LOAD 0x001000 0x00000000 0x00000000 0x20 0x20 R E 0x1000\n"
        self.assertEqual(VERIFY.first_load_paddr(text), 0)

    def test_word_at_uses_little_endian(self) -> None:
        data = b"\0" * 8 + struct.pack("<I", VERIFY.RAW_RETI1)
        self.assertEqual(VERIFY.word_at(data, 8), VERIFY.RAW_RETI1)

    def test_testb_restore_updates_c_only(self) -> None:
        word = VERIFY.RAW_TESTB_R0_1_WC
        self.assertEqual((word >> 19) & 0x3, 0x2)

    def test_augmented_address_reconstructs_full_hub_address(self) -> None:
        data = struct.pack("<II", 0xFF000018, 0xFC0000A4)
        self.assertEqual(VERIFY.augmented_address(data, 0, 4), 0x30A4)

    def test_real_assembly_contract(self) -> None:
        VERIFY.verify_assembly((HERE / "context_switch.S").read_text(encoding="utf-8"))

    def test_isr_prefix_rejects_early_gpr_clobber(self) -> None:
        assembly = (HERE / "context_switch.S").read_text(encoding="utf-8")
        broken = assembly.replace(
            "p2_context_int1:\n        augs    #0",
            "p2_context_int1:\n        mov     r1, #0\n        augs    #0",
        )
        with self.assertRaises(RuntimeError):
            VERIFY.verify_assembly(broken)

    def test_isr_rejects_allowi_before_getbrk(self) -> None:
        assembly = (HERE / "context_switch.S").read_text(encoding="utf-8")
        broken = assembly.replace(
            "        getbrk  r0                     wcz",
            "        .long   P2_RAW_ALLOWI\n"
            "        getbrk  r0                     wcz",
        )
        with self.assertRaises(RuntimeError):
            VERIFY.verify_assembly(broken)

    def test_real_startup_contract(self) -> None:
        VERIFY.verify_startup_source((HERE / "context.c").read_text(encoding="utf-8"))

    def test_real_register_window_has_terminal_escape(self) -> None:
        assembly = (HERE / "context_switch.S").read_text(encoding="utf-8")
        source = (HERE / "context.c").read_text(encoding="utf-8")
        VERIFY.verify_assembly(assembly)
        VERIFY.verify_startup_source(source)
        self.assertIn("cmp     r2, ##1000000", assembly)
        self.assertIn("mov     r0, #2", assembly)
        self.assertIn("else if (window != P2_WINDOW_TERMINAL)", source)

        broken = source.replace(
            "else if (window != P2_WINDOW_TERMINAL)",
            "else if (window != P2_WINDOW_PASS)",
        )
        with self.assertRaises(RuntimeError):
            VERIFY.verify_startup_source(broken)

    def test_outgoing_stack_args_probe(self) -> None:
        source = "return p2_vararg_sum(6u, 1u, 2u, 3u, 4u, 5u, 6u);"
        disassembly = """
00001000 <p2_task_body>:
  1000: 00 00 00 00  wrlong #5, r1
  1004: 00 00 00 00  wrlong #4, r1
  1008: 00 00 00 00  wrlong #3, r1
  100c: 00 00 00 00  wrlong #2, r1
  1010: 00 00 00 00  wrlong #1, r1
  1014: 00 00 00 00  add ptra, #28
  1018: 00 00 00 00  calla #\\p2_vararg_sum
"""
        VERIFY.verify_outgoing_stack_args(source, disassembly)

    def test_linked_isr_rejects_task_ptra_write(self) -> None:
        disassembly = """
00000ad0 <p2_context_int1>:
  ad0: 00 00 00 00  wrlong iret1, ptra++
"""
        with self.assertRaises(RuntimeError):
            VERIFY.verify_linked_isr(disassembly)

    def test_dispatch_rejects_software_arithmetic_in_hotpath(self) -> None:
        disassembly = """
00000eac <p2_context_dispatch>:
  eac: 00 00 00 00  calla #\\__mulsi3
"""
        with self.assertRaises(RuntimeError):
            VERIFY.verify_dispatch_hotpath(disassembly)

    def test_symbolic_irq_access_requires_augs(self) -> None:
        good = """
  0: 00 00 00 ff  augs #0
  4: 00 00 00 fc  wrlong r0, #0
     00000004: R_P2_AUG20 g_p2_context_irq_area
"""
        VERIFY.verify_symbolic_aug_pairs(good)
        broken = good.replace("  0: 00 00 00 ff  augs #0\n", "")
        with self.assertRaises(RuntimeError):
            VERIFY.verify_symbolic_aug_pairs(broken)

    def test_setq_block_transfer_keeps_augs_prefix(self) -> None:
        assembly = (HERE / "context_switch.S").read_text(encoding="utf-8")
        VERIFY.verify_setq_aug_block_pairs(assembly)
        broken = assembly.replace(
            "        setq    #31\n        augs    #0\n        wrlong  r0,",
            "        setq    #31\n        wrlong  r0,",
            1,
        )
        with self.assertRaises(RuntimeError):
            VERIFY.verify_setq_aug_block_pairs(broken)

    def test_outgoing_stack_args_probe_rejects_missing_advance(self) -> None:
        source = "return p2_vararg_sum(6u, 1u, 2u, 3u, 4u, 5u, 6u);"
        disassembly = """
00001000 <p2_task_body>:
  1000: 00 00 00 00  wrlong #5, r1
  1004: 00 00 00 00  wrlong #4, r1
  1008: 00 00 00 00  wrlong #3, r1
  100c: 00 00 00 00  wrlong #2, r1
  1010: 00 00 00 00  calla #\\p2_vararg_sum
"""
        with self.assertRaises(RuntimeError):
            VERIFY.verify_outgoing_stack_args(source, disassembly)


if __name__ == "__main__":
    unittest.main()
