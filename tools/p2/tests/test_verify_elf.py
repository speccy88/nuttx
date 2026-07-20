#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "tools/p2/verify-elf.py"
SPEC = importlib.util.spec_from_file_location("verify_elf", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFY_ELF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY_ELF)


class FakeRodata(dict):
    def __init__(self, start, data):
        super().__init__(sh_addr=start, sh_size=len(data))
        self._data = data

    def data(self):
        return self._data


class FakeSymbol(dict):
    def __init__(
        self,
        name,
        address,
        symbol_type="STT_FUNC",
        binding="STB_GLOBAL",
        shndx=1,
    ):
        super().__init__(
            st_value=address,
            st_info={"type": symbol_type, "bind": binding},
            st_shndx=shndx,
        )
        self.name = name


class FakeSymbolTable:
    def __init__(self, symbols):
        self._symbols = symbols

    def iter_symbols(self):
        return iter(self._symbols)


class FakeSection(dict):
    def __init__(self, name, flags):
        super().__init__(sh_flags=flags)
        self.name = name


class FakeElf:
    def __init__(self, symbols, sections):
        self._symtab = FakeSymbolTable(symbols)
        self._sections = sections

    def get_section_by_name(self, name):
        return self._symtab if name == ".symtab" else None

    def get_section(self, index):
        return self._sections.get(index)


def machine_code(words):
    return b"".join(word.to_bytes(4, "little") for word in words)


def uart_rx_cog_fixture(worker_address=0xFF0C, ring_size=256):
    launcher = [
        0xFD640228,
        0xFC67A161,
        0xF607A010,
        0xFF000000 | (worker_address >> 9),
        0xF607A200 | (worker_address & 0x1FF),
        0xFCF3A1D1,
        0xFD63DE6C,
        0xFD640228,
        0xFB07A15F,
        0xFD64002E,
    ]
    if ring_size == 256:
        worker = [0] * 38
        worker[15] = 0x3D80000E
        worker[22] = 0xF20FA500
        worker[23] = 0xAD800020
        worker[25] = 0xF507A4FF
        worker[31] = 0xFD80000E
        worker[37] = 0xFD80000E
    elif ring_size == 1024:
        worker = [0] * 40
        worker[15] = 0x3D80000E
        worker[22] = 0xFF000002
        worker[23] = 0xF20FA400
        worker[24] = 0xAD800022
        worker[26] = 0xFF000001
        worker[27] = 0xF507A5FF
        worker[33] = 0xFD80000E
        worker[39] = 0xFD80000E
    else:
        raise ValueError(f"unsupported UART RX fixture ring size {ring_size}")
    return launcher, worker


class CallableSymbolAddressVerifierTests(unittest.TestCase):
    def executable_elf(self, symbols):
        return FakeElf(
            symbols,
            {1: FakeSection(".p2.lut", VERIFY_ELF.SH_FLAGS.SHF_EXECINSTR)},
        )

    def test_rejects_public_compiler_runtime_symbol_in_lut_vma_window(self):
        elf = self.executable_elf(
            [FakeSymbol("__floatdidf", 0x29C, symbol_type="STT_NOTYPE")]
        )

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError,
            r"ambiguous LUT byte VMAs 0x200-0x3ff.*R_P2_20.*"
            r"__floatdidf=0x29c\(\.p2\.lut\)",
        ):
            VERIFY_ELF.verify_callable_symbol_addresses(elf)

    def test_rejects_local_ordinary_function_in_lut_vma_window(self):
        for address in (0x200, 0x3FF):
            with self.subTest(address=address):
                elf = self.executable_elf(
                    [
                        FakeSymbol(
                            "conversion_body",
                            address,
                            binding="STB_LOCAL",
                        )
                    ]
                )

                with self.assertRaisesRegex(
                    VERIFY_ELF.VerificationError,
                    rf"conversion_body=0x{address:x}",
                ):
                    VERIFY_ELF.verify_callable_symbol_addresses(elf)

    def test_accepts_noncallable_and_nonambiguous_symbols(self):
        elf = FakeElf(
            [
                FakeSymbol("before_lut", 0x1FF),
                FakeSymbol("hub_function", 0x400),
                FakeSymbol("lut_data", 0x220, symbol_type="STT_OBJECT"),
                FakeSymbol(
                    "local_label",
                    0x224,
                    symbol_type="STT_NOTYPE",
                    binding="STB_LOCAL",
                ),
                FakeSymbol(
                    "absolute_layout",
                    0x200,
                    symbol_type="STT_NOTYPE",
                    shndx="SHN_ABS",
                ),
                FakeSymbol(
                    "nonexec_label",
                    0x228,
                    symbol_type="STT_NOTYPE",
                    shndx=2,
                ),
            ],
            {
                1: FakeSection(".p2.lut", VERIFY_ELF.SH_FLAGS.SHF_EXECINSTR),
                2: FakeSection(".data", 0),
            },
        )

        VERIFY_ELF.verify_callable_symbol_addresses(elf)

    def test_final_elf_verifier_invokes_callable_symbol_audit(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        verify_body = source[
            source.index("def verify(path:") : source.index("def main()")
        ]

        self.assertIn("verify_callable_symbol_addresses(elf)", verify_body)


class UartRxCogVerifierTests(unittest.TestCase):
    def test_accepts_abi_guard_and_absolute_cog_long_branches(self):
        worker_address = 0xFF0C
        launcher, worker = uart_rx_cog_fixture(worker_address)

        VERIFY_ELF.verify_uart_rx_cog_machine_code(
            worker_address, machine_code(launcher), machine_code(worker)
        )

    def test_accepts_exact_1024_byte_ring_augmented_worker(self):
        worker_address = 0xFF0C
        launcher, worker = uart_rx_cog_fixture(worker_address, ring_size=1024)

        VERIFY_ELF.verify_uart_rx_cog_machine_code(
            worker_address, machine_code(launcher), machine_code(worker)
        )

    def test_rejects_nonprofile_worker_length(self):
        worker_address = 0xFF0C
        launcher, worker = uart_rx_cog_fixture(worker_address)
        worker.append(0)

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError,
            "neither the fixed 38-long.*nor the fixed 40-long",
        ):
            VERIFY_ELF.verify_uart_rx_cog_machine_code(
                worker_address, machine_code(launcher), machine_code(worker)
            )

    def test_rejects_malformed_256_byte_ring_immediates(self):
        for index in (22, 25):
            with self.subTest(index=index):
                worker_address = 0xFF0C
                launcher, worker = uart_rx_cog_fixture(worker_address)
                worker[index] ^= 1

                with self.assertRaisesRegex(
                    VERIFY_ELF.VerificationError,
                    rf"256-byte ring word {index}",
                ):
                    VERIFY_ELF.verify_uart_rx_cog_machine_code(
                        worker_address,
                        machine_code(launcher),
                        machine_code(worker),
                    )

    def test_rejects_missing_abi_pair_save(self):
        worker_address = 0xFF0C
        launcher, worker = uart_rx_cog_fixture(worker_address)
        launcher[1] = 0x00000000

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError, "p2_uart_rx_cog_start word 1"
        ):
            VERIFY_ELF.verify_uart_rx_cog_machine_code(
                worker_address, machine_code(launcher), machine_code(worker)
            )

    def test_rejects_missing_abi_pair_restore(self):
        worker_address = 0xFF0C
        launcher, worker = uart_rx_cog_fixture(worker_address)
        launcher[8] = 0x00000000

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError, "p2_uart_rx_cog_start word 8"
        ):
            VERIFY_ELF.verify_uart_rx_cog_machine_code(
                worker_address, machine_code(launcher), machine_code(worker)
            )

    def test_rejects_launcher_relocated_to_the_wrong_worker(self):
        worker_address = 0xFF0C
        launcher, worker = uart_rx_cog_fixture(worker_address + 4)

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError, "AUGS/MOV target misses its worker"
        ):
            VERIFY_ELF.verify_uart_rx_cog_machine_code(
                worker_address, machine_code(launcher), machine_code(worker)
            )

    def test_rejects_hub_relative_wait_branch(self):
        worker_address = 0xFF0C
        launcher, worker = uart_rx_cog_fixture(worker_address)
        worker[15] = 0x3D8FFFF8  # The prior linked byte-relative -8 form.

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError,
            "branch word 15.*absolute cog-long target",
        ):
            VERIFY_ELF.verify_uart_rx_cog_machine_code(
                worker_address, machine_code(launcher), machine_code(worker)
            )

    def test_rejects_wrong_absolute_drop_target(self):
        worker_address = 0xFF0C
        launcher, worker = uart_rx_cog_fixture(worker_address)
        worker[23] = 0xAD800088

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError,
            "branch word 23.*absolute cog-long target",
        ):
            VERIFY_ELF.verify_uart_rx_cog_machine_code(
                worker_address, machine_code(launcher), machine_code(worker)
            )

    def test_rejects_malformed_1024_byte_ring_augmented_immediates(self):
        malformed = {
            22: 0xFF000003,  # AUGS #3 changes the ring size.
            23: 0xF20FA401,  # Low source changes augmented #1024 to #1025.
            26: 0xFF000000,  # Missing high bits for the #1023 mask.
            27: 0xF507A5FE,  # Low source changes augmented #1023 to #1022.
        }
        for index, replacement in malformed.items():
            with self.subTest(index=index):
                worker_address = 0xFF0C
                launcher, worker = uart_rx_cog_fixture(
                    worker_address, ring_size=1024
                )
                worker[index] = replacement

                with self.assertRaisesRegex(
                    VERIFY_ELF.VerificationError,
                    rf"1024-byte ring word {index}",
                ):
                    VERIFY_ELF.verify_uart_rx_cog_machine_code(
                        worker_address,
                        machine_code(launcher),
                        machine_code(worker),
                    )

    def test_rejects_every_malformed_1024_byte_ring_branch(self):
        for index in (15, 24, 33, 39):
            with self.subTest(index=index):
                worker_address = 0xFF0C
                launcher, worker = uart_rx_cog_fixture(
                    worker_address, ring_size=1024
                )
                worker[index] ^= 1

                with self.assertRaisesRegex(
                    VERIFY_ELF.VerificationError,
                    rf"branch word {index}.*absolute cog-long target",
                ):
                    VERIFY_ELF.verify_uart_rx_cog_machine_code(
                        worker_address,
                        machine_code(launcher),
                        machine_code(worker),
                    )


class PsramRuntimeVerifierTests(unittest.TestCase):
    def test_xmem_fault_console_uses_only_raw_boot_trace_and_lowputc(self):
        symbols = {
            "__p2_xmem_boot_trace": 0x1100,
            "p2_lowputc": 0x1200,
            "__p2_xmem_load8": 0x1300,
        }

        VERIFY_ELF.verify_xmem_fault_call_graph(
            symbols, [symbols["__p2_xmem_boot_trace"]], [symbols["p2_lowputc"]]
        )

    def test_xmem_fault_console_rejects_missing_trace_call(self):
        symbols = {
            "__p2_xmem_boot_trace": 0x1100,
            "p2_lowputc": 0x1200,
        }

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError,
            "__p2_xmem_fault does not call __p2_xmem_boot_trace",
        ):
            VERIFY_ELF.verify_xmem_fault_call_graph(
                symbols, [], [symbols["p2_lowputc"]]
            )

    def test_xmem_fault_console_rejects_recursive_xmem_load(self):
        symbols = {
            "__p2_xmem_boot_trace": 0x1100,
            "p2_lowputc": 0x1200,
            "__p2_xmem_load8": 0x1300,
        }

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError,
            "__p2_xmem_boot_trace recursively calls.*__p2_xmem_load8",
        ):
            VERIFY_ELF.verify_xmem_fault_call_graph(
                symbols,
                [symbols["__p2_xmem_boot_trace"]],
                [symbols["__p2_xmem_load8"]],
            )

    def test_xmem_fault_literal_requires_exact_terminated_marker(self):
        section = FakeRodata(0x1000, b"xxxxP2XMEM:FAULT\x00yyyy")
        VERIFY_ELF.verify_xmem_fault_literal(section, 0x1004)

        wrong = FakeRodata(0x1000, b"xxxxP2XMEM:WRONG\x00yyyy")
        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError, "literal bytes mismatch"
        ):
            VERIFY_ELF.verify_xmem_fault_literal(wrong, 0x1004)

        truncated = FakeRodata(0x1000, b"xxxxP2XMEM:FAULT")
        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError, "outside Hub .rodata"
        ):
            VERIFY_ELF.verify_xmem_fault_literal(truncated, 0x1004)

    def test_cache_stats_public_wrapper_is_always_an_audit_root(self):
        symbols = {
            "p2_psram_get_cache_stats": 0x1000,
            "__p2_xmem_psram_cache_snapshot": 0x1100,
        }

        roots = VERIFY_ELF.psram_runtime_audit_roots(symbols, ())

        self.assertIn("p2_psram_get_cache_stats", roots)
        self.assertNotIn("p2_psram_unified_transfer", roots)

    def test_streamer_and_ce_accounting_are_always_audit_roots(self):
        roots = VERIFY_ELF.psram_runtime_audit_roots({}, ())

        for runtime in (
            "__p2_xmem_psram_record_ce_cycles",
            "p2_psram_stream_install",
            "p2_psram_stream_transfer",
        ):
            self.assertIn(runtime, roots)

    def test_cache_stats_wrapper_requires_native_snapshot_runtime(self):
        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError,
            "incomplete PSRAM cache stats snapshot runtime",
        ):
            VERIFY_ELF.psram_runtime_audit_roots(
                {"p2_psram_get_cache_stats": 0x1000}, ()
            )

    def test_native_call_graph_rejects_direct_wrapper_xmem_copy(self):
        canonical_copy = 0x2000

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError,
            "p2_psram_get_cache_stats recursively calls.*__p2_xmem_memcpy",
        ):
            VERIFY_ELF.verify_psram_native_call_graph(
                ["p2_psram_get_cache_stats"],
                lambda runtime: (
                    [canonical_copy]
                    if runtime == "p2_psram_get_cache_stats"
                    else []
                ),
                {},
                {canonical_copy: "__p2_xmem_memcpy"},
            )

    def test_native_call_graph_follows_psram_runtime_callees(self):
        snapshot = 0x1100
        generic_copy = 0x2100
        calls = {
            "p2_psram_get_cache_stats": [snapshot],
            "__p2_xmem_psram_cache_snapshot": [generic_copy],
        }

        with self.assertRaisesRegex(
            VERIFY_ELF.VerificationError,
            "__p2_xmem_psram_cache_snapshot recursively calls.*memcpy",
        ):
            VERIFY_ELF.verify_psram_native_call_graph(
                ["p2_psram_get_cache_stats"],
                lambda runtime: calls[runtime],
                {snapshot: ["__p2_xmem_psram_cache_snapshot"]},
                {generic_copy: "memcpy"},
            )


if __name__ == "__main__":
    unittest.main()
