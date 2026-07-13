#!/usr/bin/env python3
#
# SPDX-License-Identifier: Apache-2.0
#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.  The
# ASF licenses this file to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance with the
# License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
# License for the specific language governing permissions and limitations
# under the License.

"""Verify the load and runtime invariants of a linked P2 NuttX image."""

from __future__ import annotations

import argparse
import pathlib
import sys

from elftools.elf.constants import SH_FLAGS
from elftools.elf.elffile import ELFFile


HUB_LIMIT = 0x7C000
HUBEXEC_MIN = 0x400
TEXT_START = 0xA00
P2_ELF_MACHINE = 300
P2_ENTRY_JMP_COG_0X10 = 0xFD800010
P2_COGINIT_HUB_COG0 = 0xFCE841D0
P2_TRGINT1 = 0xFD604424
P2_ALLOWI = 0xFD604024

REQUIRED_SECTIONS = (
    ".p2.entry",
    ".p2.params",
    ".p2.cog",
    ".text",
    ".data",
    ".bss",
    ".idle_stack",
    ".initial_stack",
    ".heap",
)

REQUIRED_SYMBOLS = (
    "__entry",
    "__start",
    "p2_start",
    "__p2_loader_clkfreq",
    "__p2_loader_clkmode",
    "__p2_loader_baud",
    "_stext",
    "_etext",
    "_sdata",
    "_edata",
    "_sbss",
    "_ebss",
    "_sinitialstack",
    "_einitialstack",
    "__initial_ptra",
    "_sidle_stack",
    "_eidle_tls",
    "_eidle_stack",
    "__p2_lut_start",
    "__p2_lut_end",
    "p2_context_trigger_restore",
    "_sheap",
    "_eheap",
)

LOW_EXEC_SECTIONS = {".p2.entry", ".p2.cog", ".p2.lut"}


class VerificationError(RuntimeError):
    """An ELF invariant was violated."""


def fail(message: str) -> None:
    raise VerificationError(message)


def q_instruction_name(word: int) -> str | None:
    """Return the shared-Q instruction name encoded by a PASM2 word."""

    cordic = (word >> 20) & 0xFF
    cordic_names = (
        "QMUL",
        "QDIV",
        "QFRAC",
        "QSQRT",
        "QROTATE",
        "QVECTOR",
    )
    if 0xD0 <= cordic <= 0xD5:
        return cordic_names[cordic - 0xD0]

    opcode = (word >> 21) & 0x7F
    subopcode = word & 0x1FF
    if opcode == 0x6B:
        if ((word >> 19) & 0x3) == 0 and subopcode in (0x0E, 0x0F):
            return "QLOG" if subopcode == 0x0E else "QEXP"
        if subopcode in (0x18, 0x19):
            return "GETQX" if subopcode == 0x18 else "GETQY"

    return None


def verify_instruction_policy(sections: dict[str, object]) -> None:
    """Check raw startup opcodes and reject non-preemptible Q operations."""

    entry = sections[".p2.entry"].data()
    if len(entry) != 4:
        fail(f".p2.entry is {len(entry)} bytes; expected one instruction")

    entry_word = int.from_bytes(entry, "little")
    if entry_word != P2_ENTRY_JMP_COG_0X10:
        fail(
            f".p2.entry word is 0x{entry_word:08x}; expected COGEXEC "
            f"JMP #0x10 word 0x{P2_ENTRY_JMP_COG_0X10:08x}"
        )

    cog = sections[".p2.cog"].data()
    if len(cog) < 12:
        fail(".p2.cog is too short to contain the Hub-mode COGINIT")

    coginit_word = int.from_bytes(cog[8:12], "little")
    if coginit_word != P2_COGINIT_HUB_COG0:
        fail(
            f".p2.cog COGINIT word is 0x{coginit_word:08x}; expected "
            f"Hub-mode cog-0 word 0x{P2_COGINIT_HUB_COG0:08x}"
        )

    for section in sections.values():
        flags = int(section["sh_flags"])
        if not flags & SH_FLAGS.SHF_EXECINSTR:
            continue

        data = section.data()
        base = int(section["sh_addr"])
        for offset in range(0, len(data) - 3, 4):
            word = int.from_bytes(data[offset : offset + 4], "little")
            name = q_instruction_name(word)
            if name is not None:
                fail(
                    f"forbidden shared-Q instruction {name} at "
                    f"0x{base + offset:x} in {section.name} "
                    f"(word 0x{word:08x})"
                )


def decode_augmented_source(aug: int, instruction: int) -> int:
    """Decode the absolute source formed by an AUGS and its next word."""

    if aug & 0xFF800000 != 0xFF000000:
        fail(f"expected AUGS word, found 0x{aug:08x}")
    return ((aug & 0x7FFFFF) << 9) | (instruction & 0x1FF)


def verify_startup(sections: dict[str, object], symbols: dict[str, int]) -> None:
    """Verify every linked target in the COGEXEC-to-HUBEXEC bootstrap."""

    params = sections[".p2.params"].data()
    if len(params) != 12 or params != bytes(12):
        fail(".p2.params must contain exactly three linked-zero loader words")

    loader_symbols = (
        "__p2_loader_clkfreq",
        "__p2_loader_clkmode",
        "__p2_loader_baud",
    )
    for name, address in zip(loader_symbols, (0x14, 0x18, 0x1C)):
        if symbols[name] != address:
            fail(f"{name} is 0x{symbols[name]:x}; expected 0x{address:x}")

    cog = sections[".p2.cog"].data()
    cog_aug = int.from_bytes(cog[0:4], "little")
    cog_mov = int.from_bytes(cog[4:8], "little")
    if cog_mov & ~0x1FF != 0xF607A000:
        fail(f".p2.cog does not load __start into r0 (word 0x{cog_mov:08x})")
    if decode_augmented_source(cog_aug, cog_mov) != symbols["__start"]:
        fail(".p2.cog AUGS/MOV target does not match __start")

    text = sections[".text"]
    start_offset = symbols["__start"] - int(text["sh_addr"])
    start = text.data()[start_offset : start_offset + 12]
    if len(start) != 12:
        fail("__start is too short for its AUGS/MOV/CALLA sequence")

    stack_aug = int.from_bytes(start[0:4], "little")
    stack_mov = int.from_bytes(start[4:8], "little")
    start_call = int.from_bytes(start[8:12], "little")
    if stack_mov & ~0x1FF != 0xF607F000:
        fail(
            "__start does not load __initial_ptra into PTRA "
            f"(word 0x{stack_mov:08x})"
        )
    if decode_augmented_source(stack_aug, stack_mov) != \
            symbols["__initial_ptra"]:
        fail("__start AUGS/MOV target does not match __initial_ptra")
    if start_call & 0xFFF00000 != 0xFDC00000:
        fail(f"__start third word is not CALLA (word 0x{start_call:08x})")
    if start_call & 0xFFFFF != symbols["p2_start"]:
        fail("__start CALLA target does not match p2_start")


def verify_context_trigger(
    sections: dict[str, object], symbols: dict[str, int]
) -> None:
    """Require the stalled-to-waiting INT1 restore handoff sequence."""

    address = symbols["p2_context_trigger_restore"]
    code = None
    for section in sections.values():
        start = int(section["sh_addr"])
        size = int(section["sh_size"])
        if int(section["sh_flags"]) & SH_FLAGS.SHF_EXECINSTR and \
                start <= address and address + 12 <= start + size:
            offset = address - start
            code = section.data()[offset : offset + 12]
            break

    if code is None:
        fail("p2_context_trigger_restore is outside executable sections")

    trigger = int.from_bytes(code[0:4], "little")
    allow = int.from_bytes(code[4:8], "little")
    wait = int.from_bytes(code[8:12], "little")
    if trigger != P2_TRGINT1 or allow != P2_ALLOWI:
        fail(
            "p2_context_trigger_restore must start with TRGINT1/ALLOWI; "
            f"found 0x{trigger:08x}/0x{allow:08x}"
        )
    if wait & 0xFFF00000 != 0xFD800000 or \
            wait & 0xFFFFF != address + 8:
        fail(
            "p2_context_trigger_restore does not spin after enabling its "
            f"waiting interrupt (word 0x{wait:08x})"
        )


def executable_code(
    sections: dict[str, object], address: int, length: int, name: str
) -> bytes:
    """Return one symbol's bytes from its containing executable section."""

    for section in sections.values():
        start = int(section["sh_addr"])
        size = int(section["sh_size"])
        if int(section["sh_flags"]) & SH_FLAGS.SHF_EXECINSTR and \
                start <= address and address + length <= start + size:
            offset = address - start
            return section.data()[offset : offset + length]

    fail(f"{name} is outside executable sections or is truncated")


def function_code(
    elf: ELFFile,
    sections: dict[str, object],
    symbols: dict[str, int],
    name: str,
) -> bytes:
    """Return the complete linked body of one function symbol."""

    symtab = elf.get_section_by_name(".symtab")
    if symtab is None:
        fail("missing .symtab")

    for symbol in symtab.iter_symbols():
        if symbol.name == name and symbol["st_info"]["type"] == "STT_FUNC":
            size = int(symbol["st_size"])
            if size <= 0:
                fail(f"{name} has no linked function size")
            return executable_code(sections, symbols[name], size, name)

    fail(f"missing function symbol {name}")


def direct_calla_targets(code: bytes) -> list[int]:
    """Return absolute immediate targets of linked CALLA instructions."""

    targets = []
    for offset in range(0, len(code) - 3, 4):
        word = int.from_bytes(code[offset : offset + 4], "little")
        if word & 0xFFF00000 == 0xFDC00000:
            targets.append(word & 0xFFFFF)
    return targets


def verify_psram_test_hotpath(
    elf: ELFFile, sections: dict[str, object], symbols: dict[str, int]
) -> None:
    """Keep the optional 32-MiB PSRAM test out of slow multiply helpers."""

    entry = "p2psram_full_coverage"
    if entry not in symbols:
        return

    required = (entry, "p2psram_hash", "p2psram_fill", "__mulsi3")
    missing = [name for name in required if name not in symbols]
    if missing:
        fail(f"incomplete PSRAM test symbols: {', '.join(missing)}")

    if "p2psram_pattern_next" in symbols:
        fail("p2psram_pattern_next was not inlined out of the byte hot path")

    multiply = symbols["__mulsi3"]
    code = {
        name: function_code(elf, sections, symbols, name)
        for name in (entry, "p2psram_hash", "p2psram_fill")
    }
    for name, body in code.items():
        if multiply in direct_calla_targets(body):
            fail(f"{name} calls slow __mulsi3 in the PSRAM full-pass path")

    full_targets = direct_calla_targets(code[entry])
    for name in ("p2psram_hash", "p2psram_fill"):
        if symbols[name] not in full_targets:
            fail(f"{entry} does not call the locked {name} helper")


def verify_psram_service(
    sections: dict[str, object], symbols: dict[str, int]
) -> None:
    """Verify optional PSRAM service-cog AUGS relocation pairs."""

    required = (
        "p2_psram_cog_start",
        "p2_psram_cog_entry",
        "p2_psram_timing_leaf",
        "p2_psram_service_worker",
        "g_p2_psram_service_stack",
        "g_p2_psram_wire",
    )
    present = [name for name in required if name in symbols]
    if not present:
        return

    missing = [name for name in required if name not in symbols]
    if missing:
        fail(f"incomplete PSRAM service symbols: {', '.join(missing)}")

    start = executable_code(
        sections, symbols["p2_psram_cog_start"], 40, "p2_psram_cog_start"
    )
    start_words = [
        int.from_bytes(start[offset : offset + 4], "little")
        for offset in range(0, len(start), 4)
    ]
    fixed_start = {
        0: 0xFD640228,  # SETQ #1
        1: 0xFC67A161,  # WRLONG r0, PTRA++
        2: 0xF607A030,  # MOV r0, #0x30 (HUBEXEC_NEW)
        5: 0xFCF3A1D1,  # COGINIT r0, r1 WC
        6: 0xF603DFD0,  # MOV r31, r0
        7: 0xFD640228,  # SETQ #1
        8: 0xFB07A15F,  # RDLONG r0, --PTRA
        9: 0xFD64002E,  # RETA
    }
    for index, expected in fixed_start.items():
        if start_words[index] != expected:
            fail(
                "p2_psram_cog_start word {} is 0x{:08x}; expected "
                "0x{:08x}".format(index, start_words[index], expected)
            )

    if start_words[4] & ~0x1FF != 0xF607A200:
        fail("p2_psram_cog_start does not load its entry into r1")
    if decode_augmented_source(start_words[3], start_words[4]) != \
            symbols["p2_psram_cog_entry"]:
        fail("p2_psram_cog_start AUGS/MOV target does not match its entry")

    entry = executable_code(
        sections, symbols["p2_psram_cog_entry"], 24, "p2_psram_cog_entry"
    )
    entry_words = [
        int.from_bytes(entry[offset : offset + 4], "little")
        for offset in range(0, len(entry), 4)
    ]
    if entry_words[1] & ~0x1FF != 0xF607F000:
        fail("p2_psram_cog_entry does not load its guarded PTRA")
    if decode_augmented_source(entry_words[0], entry_words[1]) != \
            symbols["g_p2_psram_service_stack"]:
        fail("p2_psram_cog_entry AUGS/MOV target does not match its stack")
    if entry_words[2] != 0xF107F004:
        fail("p2_psram_cog_entry does not advance its guarded PTRA")
    if entry_words[3] & 0xFFF00000 != 0xFDC00000 or \
            entry_words[3] & 0xFFFFF != symbols["p2_psram_service_worker"]:
        fail("p2_psram_cog_entry CALLA target does not match its worker")
    if entry_words[4:] != [0xFD63A001, 0xFD63A003]:
        fail("p2_psram_cog_entry does not terminate with COGID/COGSTOP")

    timing = executable_code(
        sections, symbols["p2_psram_timing_leaf"], 16,
        "p2_psram_timing_leaf",
    )
    timing_words = [
        int.from_bytes(timing[offset : offset + 4], "little")
        for offset in range(0, len(timing), 4)
    ]
    if timing_words[:2] != [0xFD641E28, 0xFC67A161]:
        fail("p2_psram_timing_leaf lost its SETQ/WRLONG register save")
    if timing_words[3] & ~0x1FF != 0xF607BE00:
        fail("p2_psram_timing_leaf does not load its wire descriptor into r15")
    if decode_augmented_source(timing_words[2], timing_words[3]) != \
            symbols["g_p2_psram_wire"]:
        fail("p2_psram_timing_leaf AUGS/MOV target does not match its wire data")


def symbol_values(elf: ELFFile) -> tuple[dict[str, int], list[str]]:
    symbols: dict[str, int] = {}
    unresolved: list[str] = []
    symtab = elf.get_section_by_name(".symtab")

    if symtab is None:
        fail("missing .symtab")

    for symbol in symtab.iter_symbols():
        name = symbol.name
        if not name:
            continue

        shndx = symbol["st_shndx"]
        bind = symbol["st_info"]["bind"]
        if shndx == "SHN_UNDEF" and bind != "STB_LOCAL":
            unresolved.append(name)
        elif shndx != "SHN_UNDEF":
            symbols.setdefault(name, int(symbol["st_value"]))

    return symbols, sorted(set(unresolved))


def verify_sections(elf: ELFFile) -> dict[str, object]:
    sections = {section.name: section for section in elf.iter_sections()}
    missing = [name for name in REQUIRED_SECTIONS if name not in sections]
    if missing:
        fail(f"missing required sections: {', '.join(missing)}")

    relocation_sections = [
        section.name
        for section in elf.iter_sections()
        if section["sh_type"] in ("SHT_REL", "SHT_RELA")
    ]
    if relocation_sections:
        fail(f"linked ELF retains relocations: {', '.join(relocation_sections)}")

    for name in (".p2.entry", ".p2.cog", ".text"):
        if sections[name]["sh_size"] == 0:
            fail(f"required executable section {name} is empty")

    expected_addresses = {
        ".p2.entry": 0,
        ".p2.cog": 0x40,
        ".text": TEXT_START,
    }
    for name, expected in expected_addresses.items():
        actual = int(sections[name]["sh_addr"])
        if actual != expected:
            fail(f"{name} starts at 0x{actual:x}; expected 0x{expected:x}")

    # The pinned p2llvm linker ABI reserves LUT byte address 0x200.  The
    # NuttX-qualified compiler uses Hub calls for all helpers, so an image
    # with no selected LUT runtime legitimately has no .p2.lut output
    # section.  If material is selected, it must start at the fixed address.

    if ".p2.lut" in sections:
        actual = int(sections[".p2.lut"]["sh_addr"])
        if actual != 0x200:
            fail(f".p2.lut starts at 0x{actual:x}; expected 0x200")

    allocated: list[tuple[int, int, str]] = []
    for section in elf.iter_sections():
        flags = int(section["sh_flags"])
        size = int(section["sh_size"])
        start = int(section["sh_addr"])
        end = start + size

        if flags & SH_FLAGS.SHF_EXECINSTR:
            if start < HUBEXEC_MIN and section.name not in LOW_EXEC_SECTIONS:
                fail(
                    f"ordinary executable section {section.name} starts below "
                    f"Hub-exec address 0x{HUBEXEC_MIN:x}"
                )

        if flags & SH_FLAGS.SHF_ALLOC and size:
            if start < 0 or end > HUB_LIMIT:
                fail(
                    f"allocated section {section.name} range "
                    f"0x{start:x}-0x{end:x} exceeds Hub RAM"
                )
            allocated.append((start, end, section.name))

    allocated.sort()
    for previous, current in zip(allocated, allocated[1:]):
        if current[0] < previous[1]:
            fail(
                f"allocated sections overlap: {previous[2]} ends at "
                f"0x{previous[1]:x}, {current[2]} starts at 0x{current[0]:x}"
            )

    return sections


def verify_segments(elf: ELFFile, sections: dict[str, object]) -> int:
    loads = [
        segment
        for segment in elf.iter_segments()
        if segment["p_type"] == "PT_LOAD" and int(segment["p_filesz"]) > 0
    ]
    if not loads:
        fail("ELF has no nonempty PT_LOAD segment")

    first = loads[0]
    if int(first["p_paddr"]) != 0:
        fail(
            "first nonempty PT_LOAD has physical address "
            f"0x{int(first['p_paddr']):x}; loadp2 requires 0"
        )

    ranges: list[tuple[int, int]] = []
    for segment in loads:
        paddr = int(segment["p_paddr"])
        vaddr = int(segment["p_vaddr"])
        memsz = int(segment["p_memsz"])
        filesz = int(segment["p_filesz"])
        if vaddr != paddr:
            fail(
                f"PT_LOAD virtual address 0x{vaddr:x} differs from physical "
                f"address 0x{paddr:x}"
            )
        if filesz > memsz:
            fail("PT_LOAD file size exceeds memory size")
        if paddr + memsz > HUB_LIMIT:
            fail(
                f"PT_LOAD range 0x{paddr:x}-0x{paddr + memsz:x} exceeds Hub RAM"
            )
        ranges.append((paddr, paddr + memsz))

    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] < previous[1]:
            fail(
                f"PT_LOAD ranges overlap at 0x{current[0]:x}: previous ends "
                f"at 0x{previous[1]:x}"
            )

    for section in sections.values():
        flags = int(section["sh_flags"])
        size = int(section["sh_size"])
        if not flags & SH_FLAGS.SHF_ALLOC or size == 0:
            continue

        start = int(section["sh_addr"])
        end = start + size
        if not any(start >= low and end <= high for low, high in ranges):
            fail(
                f"allocated section {section.name} range 0x{start:x}-0x{end:x} "
                "is not covered by a PT_LOAD"
            )

    return len(loads)


def ranges_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] < second[1] and second[0] < first[1]


def verify_symbols(elf: ELFFile) -> dict[str, int]:
    symbols, unresolved = symbol_values(elf)
    if unresolved:
        fail(f"unresolved symbols: {', '.join(unresolved)}")

    missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
    if missing:
        fail(f"missing required symbols: {', '.join(missing)}")

    entry = int(elf.header["e_entry"])
    if entry != symbols["__entry"] or entry != 0:
        fail(
            f"ELF entry is 0x{entry:x}, __entry is 0x{symbols['__entry']:x}; "
            "both must be zero"
        )

    stack = (symbols["_sinitialstack"], symbols["_einitialstack"])
    heap = (symbols["_sheap"], symbols["_eheap"])
    for name, region in (("initial stack", stack), ("heap", heap)):
        if not (0 <= region[0] < region[1] <= HUB_LIMIT):
            fail(f"invalid {name} range 0x{region[0]:x}-0x{region[1]:x}")

    if ranges_overlap(stack, heap):
        fail("initial stack overlaps heap")

    ptra = symbols["__initial_ptra"]
    if ptra != stack[0]:
        fail(
            f"initial PTRA is 0x{ptra:x}; upward stack starts at 0x{stack[0]:x}"
        )

    idle = (symbols["_sidle_stack"], symbols["_eidle_tls"])
    if not (0 <= idle[0] < idle[1] == stack[0]):
        fail(
            f"invalid idle TLS range 0x{idle[0]:x}-0x{idle[1]:x}; "
            f"initial stack starts at 0x{stack[0]:x}"
        )
    if symbols["_eidle_stack"] != stack[1] or stack[1] != heap[0]:
        fail("idle allocation, initial stack, and heap are not contiguous")
    if symbols["_eheap"] != HUB_LIMIT:
        fail(
            f"heap ends at 0x{symbols['_eheap']:x}; expected loader-safe "
            f"limit 0x{HUB_LIMIT:x}"
        )

    return symbols


def verify(path: pathlib.Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        fail(f"ELF is missing or empty: {path}")

    with path.open("rb") as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 32 or not elf.little_endian:
            fail("P2 ELF must be 32-bit little-endian")
        if int(elf.header["e_machine"]) != P2_ELF_MACHINE:
            fail(
                f"ELF machine is {int(elf.header['e_machine'])}; "
                f"expected P2 machine {P2_ELF_MACHINE}"
            )

        sections = verify_sections(elf)
        verify_instruction_policy(sections)
        symbols = verify_symbols(elf)
        verify_startup(sections, symbols)
        verify_context_trigger(sections, symbols)
        verify_psram_service(sections, symbols)
        verify_psram_test_hotpath(elf, sections, symbols)
        load_count = verify_segments(elf, sections)

        print(
            "P2 ELF verification: PASS\n"
            f"entry=0x{int(elf.header['e_entry']):x}\n"
            f"load_segments={load_count}\n"
            f"text=0x{int(sections['.text']['sh_addr']):x}-"
            f"0x{int(sections['.text']['sh_addr']) + int(sections['.text']['sh_size']):x}\n"
            f"heap=0x{symbols['_sheap']:x}-0x{symbols['_eheap']:x}\n"
            f"initial_stack=0x{symbols['_sinitialstack']:x}-"
            f"0x{symbols['_einitialstack']:x}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=pathlib.Path)
    args = parser.parse_args()

    try:
        verify(args.elf)
    except (OSError, VerificationError, ValueError) as error:
        print(f"P2 ELF verification: FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
