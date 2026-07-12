#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Static verification for the native P2 CT1 context-switch proof."""

from __future__ import annotations

import argparse
import re
import struct
import subprocess
import sys
from pathlib import Path

RAW_ALLOWI = 0xFD604024
RAW_STALLI = 0xFD604224
RAW_SETINT1_CT1 = 0xFD640225
RAW_SETINT1_OFF = 0xFD640025
RAW_GETBRK_R0_WCZ = 0xFD7BA035
RAW_TESTB_R0_1_WC = 0xF417A001
RAW_RETI1 = 0xFB3BFFF5


def run(tool: Path, *args: str) -> str:
    result = subprocess.run(
        [str(tool), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join((str(tool), *args))} failed:\n{result.stdout}")

    return result.stdout


def parse_symbols(output: str) -> tuple[dict[str, int], list[str]]:
    symbols: dict[str, int] = {}
    undefined: list[str] = []

    for line in output.splitlines():
        match = re.match(
            r"\s*\d+:\s+([0-9a-fA-F]+)\s+\d+\s+\S+\s+\S+\s+\S+\s+" r"(\S+)\s*(.*)$",
            line,
        )
        if match is None:
            continue

        value, section, name = match.groups()
        name = name.split("@", 1)[0]
        if not name:
            continue
        if section == "UND":
            undefined.append(name)
        else:
            symbols[name] = int(value, 16)

    return symbols, undefined


def first_load_paddr(output: str) -> int:
    for line in output.splitlines():
        match = re.match(
            r"\s*LOAD\s+0x[0-9a-fA-F]+\s+0x[0-9a-fA-F]+\s+" r"(0x[0-9a-fA-F]+)",
            line,
        )
        if match is not None:
            return int(match.group(1), 16)

    raise RuntimeError("ELF has no PT_LOAD program header")


def derive_setint1_immediate(event: int) -> int:
    if not 0 <= event <= 0x1FF:
        raise ValueError("SETINT1 event is outside its 9-bit immediate field")
    return 0xFD640025 | (event << 9)


def require_order(text: str, tokens: list[str], description: str) -> None:
    position = 0
    for token in tokens:
        found = text.find(token, position)
        if found < 0:
            raise RuntimeError(f"{description} is missing ordered token {token!r}")
        position = found + len(token)


def verify_assembly(text: str) -> None:
    if derive_setint1_immediate(0) != RAW_SETINT1_OFF:
        raise RuntimeError("SETINT1 #0 derivation does not equal 0xfd640025")
    if derive_setint1_immediate(1) != RAW_SETINT1_CT1:
        raise RuntimeError("SETINT1 #1 derivation does not equal 0xfd640225")

    required_defines = {
        "P2_RAW_ALLOWI": RAW_ALLOWI,
        "P2_RAW_STALLI": RAW_STALLI,
        "P2_RAW_SETINT1_CT1": RAW_SETINT1_CT1,
        "P2_RAW_SETINT1_OFF": RAW_SETINT1_OFF,
        "P2_RAW_RETI1": RAW_RETI1,
    }
    for name, value in required_defines.items():
        pattern = rf"#define\s+{name}\s+0x{value:08x}\b"
        if re.search(pattern, text, re.IGNORECASE) is None:
            raise RuntimeError(f"assembly does not define exact {name}")

    enable_match = re.search(
        r"^p2_context_timer_enable:\s*(.*?)" r"^\s*\.size\s+p2_context_timer_enable",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if enable_match is None:
        raise RuntimeError("cannot locate p2_context_timer_enable source body")
    if "P2_RAW_ALLOWI" in enable_match.group(1):
        raise RuntimeError("timer enable exposes the boot stack before launch")

    isr_match = re.search(
        r"^p2_context_int1:\s*(.*?)^\s*\.size\s+p2_context_int1",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if isr_match is None:
        raise RuntimeError("cannot locate p2_context_int1 source body")
    isr = isr_match.group(1)
    getbrk = isr.find("getbrk")
    if getbrk < 0 or "wcz" not in isr[getbrk : getbrk + 80]:
        raise RuntimeError("ISR does not use GETBRK with WCZ")
    if "P2_RAW_STALLI" in isr[:getbrk] or "P2_RAW_ALLOWI" in isr[:getbrk]:
        raise RuntimeError("ISR changes interrupt state before GETBRK")
    if "ptra++" in isr or "--ptra" in isr:
        raise RuntimeError("ISR writes an interrupt frame through task PTRA")
    if isr.count("augs    #0") < 16:
        raise RuntimeError("ISR absolute scratch operations lack AUGS prefixes")

    first_gpr_use = isr.find("        mov     r0, ptra")
    if first_gpr_use < 0:
        raise RuntimeError("ISR lacks post-save task-PTRA snapshot")
    prefix_instructions = re.findall(
        r"^\s*(augs|wrlong|setq|mov|rdlong|add|sub|and|getbrk)\b",
        isr[:first_gpr_use],
        re.MULTILINE,
    )
    expected_prefix = [
        "augs",
        "wrlong",
        "setq",
        "augs",
        "wrlong",
        "augs",
        "wrlong",
        "augs",
        "wrlong",
        "augs",
        "wrlong",
        "augs",
        "wrlong",
    ]
    if prefix_instructions != expected_prefix:
        raise RuntimeError("ISR clobbers task-visible state before full save")

    require_order(
        isr,
        [
            "wrlong  iret1, ##(g_p2_context_irq_area",
            "setq    #31",
            "wrlong  r0, ##(g_p2_context_irq_area",
            "wrlong  pa, ##(g_p2_context_irq_area",
            "wrlong  pb, ##(g_p2_context_irq_area",
            "wrlong  ptra, ##(g_p2_context_irq_area",
            "wrlong  ptrb, ##(g_p2_context_irq_area",
            "mov     r0, ptra",
            "add     r0, #4",
            "P2_IRQ_FRAME_PTRA)",
            "getbrk  r0",
            "and     r0, #P2_IRQSTATE_STALLED",
            "P2_IRQ_FRAME_IRQSTATE)",
            "mov     ptra, ##(g_p2_context_irq_stack",
            "calla   #\\p2_context_dispatch",
        ],
        "absolute-scratch ISR save path",
    )
    require_order(
        isr,
        [
            "rdlong  r0, ##(g_p2_context_irq_area",
            "P2_IRQ_FRAME_IRQSTATE)",
            "rdlong  ptrb, ##(g_p2_context_irq_area",
            "rdlong  pb, ##(g_p2_context_irq_area",
            "rdlong  pa, ##(g_p2_context_irq_area",
            "setq    #31",
            "rdlong  r0, ##(g_p2_context_irq_area",
            "rdlong  iret1, ##(g_p2_context_irq_area",
            "rdlong  ptra, ##(g_p2_context_irq_area",
            "sub     ptra, #4",
            ".long   P2_RAW_RETI1",
        ],
        "absolute-scratch ISR restore path",
    )
    restore = isr[isr.find("calla   #\\p2_context_dispatch") :]
    if re.search(r"testb\s+r0,\s*#1\s+wc", restore) is None:
        raise RuntimeError("ISR restore does not rebuild STALLI decision in C")

    pattern_match = re.search(
        r"^p2_context_register_window:\s*(.*?)"
        r"^\s*\.size\s+p2_context_register_window",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if pattern_match is None:
        raise RuntimeError("cannot locate register-window source body")
    require_order(
        pattern_match.group(1),
        [
            ".Lpattern_wait:",
            "add     r2, #8",
            "mov     r0, #0",
            "cmp     r2, ##1000000",
            "mov     r2, ##1000000",
            "mov     r0, #2",
            ".Lpattern_wait_loop:",
            "cmp     r3, r2",
            ".Lpattern_pass:",
            "mov     r31, r0",
        ],
        "terminal-safe register window",
    )


def verify_startup_source(text: str) -> None:
    for definition in (
        "#define P2_WINDOW_PASS             0u",
        "#define P2_WINDOW_FAIL             1u",
        "#define P2_WINDOW_TERMINAL         2u",
    ):
        if definition not in text:
            raise RuntimeError(
                f"source is missing register-window status {definition!r}"
            )

    main_match = re.search(
        r"^int main\(void\)\s*\{(.*)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if main_match is None:
        raise RuntimeError("cannot locate standalone main source body")

    require_order(
        main_match.group(1),
        [
            "p2_context_timer_quiesce();",
            'p2_emit("P2CTX:ENTRY',
            "p2_irq_storage_initialize();",
            "task0_frame = p2_synthetic_frame(0u, p2_task0);",
            "p2_synthetic_frame(1u, p2_task1);",
            'p2_emit("P2CTX:START\\r\\n");',
            'p2_emit("P2CTX:TARGET=1000000',
            "deadline = p2_counter();",
            "addct1(deadline, P2_TIMER_PERIOD_CYCLES);",
            "p2_context_timer_enable(p2_context_int1);",
            "p2_context_start(task0_frame);",
        ],
        "stalled synthetic-task launch",
    )

    exact_markers = [
        "P2CTX:START\\r\\n",
        "P2CTX:SWITCHES=",
        "P2CTX:REGS=OK\\r\\n",
        "P2CTX:STACKS=OK\\r\\n",
        "P2CTX:PASS\\r\\n",
    ]
    for marker in exact_markers:
        if marker not in text:
            raise RuntimeError(f"source is missing exact HIL marker {marker!r}")

    require_order(
        text,
        [
            "window = p2_context_register_window",
            "if (window == P2_WINDOW_FAIL)",
            "g_failures |= P2_FAIL_REGPATTERN;",
            "else if (window == P2_WINDOW_PASS)",
            "g_register_windows[task]++;",
            "else if (window != P2_WINDOW_TERMINAL)",
            "g_failures |= P2_FAIL_REGPATTERN;",
        ],
        "register-window tri-state handling",
    )

    for token in (
        "g_p2_context_irq_area[P2_IRQ_AREA_LONGS]",
        "g_p2_context_irq_stack[P2_IRQ_STACK_LONGS]",
        "g_detached_frames[P2_TASK_COUNT][P2_FRAME_TOTAL_LONGS]",
        "p2_irq_storage_valid()",
        "P2CTX:IRQ_CANARIES=OK\\r\\n",
    ):
        if token not in text:
            raise RuntimeError(f"source is missing detached IRQ storage {token!r}")


def disassembly_function(text: str, name: str) -> str:
    match = re.search(
        rf"^[0-9a-fA-F]+ <{re.escape(name)}>:\s*(.*?)"
        rf"(?=^[0-9a-fA-F]+ <|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise RuntimeError(f"cannot locate disassembly for {name}")
    return match.group(1)


def verify_linked_isr(disassembly: str) -> None:
    body = disassembly_function(disassembly, "p2_context_int1")
    forbidden_ptra = ("ptra++", "--ptra", "ptra[", "ptrb[")
    if any(token in body for token in forbidden_ptra):
        raise RuntimeError("linked ISR still writes through task PTRA")
    if body.count("augs #") < 16:
        raise RuntimeError("linked ISR lacks absolute-address AUGS operations")
    require_order(
        body,
        [
            "wrlong iret1",
            "setq #31",
            "wrlong r0",
            "wrlong pa",
            "wrlong pb",
            "wrlong ptra",
            "wrlong ptrb",
            "mov r0, ptra",
            "getbrk r0",
            "mov ptra",
            "calla #\\p2_context_dispatch",
            "rdlong ptrb",
            "rdlong pb",
            "rdlong pa",
            "setq #31",
            "rdlong r0",
            "rdlong iret1",
            "rdlong ptra",
        ],
        "linked detached-frame ISR",
    )


def verify_dispatch_hotpath(disassembly: str) -> None:
    body = disassembly_function(disassembly, "p2_context_dispatch")
    for helper in ("__mulsi3", "__umodsi3", "__divsi3", "__udivsi3"):
        if f"calla #\\{helper}" in body:
            raise RuntimeError(
                f"context dispatcher hot path still calls {helper}"
            )


def verify_symbolic_aug_pairs(object_disassembly: str) -> None:
    instructions: list[str] = []
    for line in object_disassembly.splitlines():
        instruction = re.match(
            r"\s*[0-9a-fA-F]+:\s+(?:[0-9a-fA-F]{2}\s+){4}\s*(.*)$", line
        )
        if instruction is not None:
            instructions.append(instruction.group(1).strip())
            continue

        if "R_P2_AUG20" in line and "g_p2_context_irq_" in line:
            if len(instructions) < 2 or not instructions[-2].startswith("augs #"):
                raise RuntimeError(
                    "symbolic IRQ scratch access is not immediately AUGS-prefixed"
                )


def verify_setq_aug_block_pairs(assembly: str) -> None:
    patterns = (
        r"setq\s+#31\s*\n\s*augs\s+#0\s*\n\s*wrlong\s+r0,",
        r"setq\s+#31\s*\n\s*augs\s+#0\s*\n\s*rdlong\s+r0,",
    )
    for pattern in patterns:
        if re.search(pattern, assembly) is None:
            raise RuntimeError("SETQ block transfer is not followed by AUGS access")


def verify_outgoing_stack_args(source: str, disassembly: str) -> None:
    if "p2_vararg_sum(6u" not in source:
        raise RuntimeError("source no longer forces outgoing stack arguments")

    body = disassembly_function(disassembly, "p2_task_body")
    call = body.find("calla #\\p2_vararg_sum")
    if call < 0:
        raise RuntimeError("task body does not call the variadic stack-arg probe")
    advance = body.rfind("add ptra, #28", 0, call)
    if advance < 0:
        raise RuntimeError("stack-arg probe lacks delayed PTRA advance")
    if body[:advance].count("wrlong #") < 4:
        raise RuntimeError("stack-arg probe lacks outgoing stores before PTRA advance")


def word_at(binary: bytes, address: int) -> int:
    if address < 0 or address + 4 > len(binary):
        raise RuntimeError(f"raw-word address {address:#x} is outside binary")
    return struct.unpack_from("<I", binary, address)[0]


def augmented_address(binary: bytes, aug_address: int, op_address: int) -> int:
    aug = word_at(binary, aug_address)
    operation = word_at(binary, op_address)
    if (aug & 0xFF800000) != 0xFF000000:
        raise RuntimeError(f"word at {aug_address:#x} is not AUGS")
    return ((aug & 0x007FFFFF) << 9) | (operation & 0x1FF)


def verify_absolute_scratch_addresses(
    binary: bytes, symbols: dict[str, int]
) -> None:
    isr = symbols["p2_context_int1"]
    area = symbols["g_p2_context_irq_area"]
    irq_stack = symbols["g_p2_context_irq_stack"]
    if (area & 0x1FF) != 0 or (irq_stack & 0x1FF) != 0:
        raise RuntimeError("IRQ scratch/stack symbols are not 512-byte aligned")

    accesses = [
        (0, 4, area + 16, "save resume"),
        (12, 16, area + 20, "save r0-r31"),
        (20, 24, area + 148, "save PA"),
        (28, 32, area + 152, "save PB"),
        (36, 40, area + 156, "save raw PTRA"),
        (44, 48, area + 160, "save PTRB"),
        (60, 64, area + 156, "save logical PTRA"),
        (76, 80, area + 164, "save IRQ state"),
        (84, 88, irq_stack + 64, "select IRQ C stack"),
        (96, 100, area + 164, "restore IRQ state"),
        (108, 112, area + 160, "restore PTRB"),
        (116, 120, area + 152, "restore PB"),
        (124, 128, area + 148, "restore PA"),
        (136, 140, area + 20, "restore r0-r31"),
        (144, 148, area + 16, "restore resume"),
        (152, 156, area + 156, "restore PTRA"),
    ]
    for aug_offset, op_offset, expected, description in accesses:
        actual = augmented_address(binary, isr + aug_offset, isr + op_offset)
        if actual != expected:
            raise RuntimeError(
                f"{description} resolves to {actual:#x}, expected {expected:#x}"
            )


def verify_raw_words(binary: bytes, symbols: dict[str, int]) -> None:
    checks = [
        ("p2_context_timer_enable", 4, RAW_SETINT1_CT1, "SETINT1 #1"),
        ("p2_context_timer_mask", 0, RAW_SETINT1_OFF, "SETINT1 #0"),
        ("p2_context_timer_quiesce", 0, RAW_STALLI, "STALLI"),
        ("p2_context_timer_quiesce", 4, RAW_SETINT1_OFF, "SETINT1 #0"),
        ("p2_context_start", 8, RAW_TESTB_R0_1_WC, "TESTB r0,#1 WC"),
        ("p2_context_start", 40, RAW_ALLOWI, "ALLOWI"),
        ("p2_context_int1", 68, RAW_GETBRK_R0_WCZ, "GETBRK r0 WCZ"),
        ("p2_context_int1", 104, RAW_TESTB_R0_1_WC, "TESTB r0,#1 WC"),
        ("p2_context_int1", 180, RAW_RETI1, "RETI1"),
    ]
    for symbol, offset, expected, operation in checks:
        actual = word_at(binary, symbols[symbol] + offset)
        if actual != expected:
            raise RuntimeError(
                f"{operation} at {symbol}+{offset:#x} is {actual:#010x}, "
                f"expected {expected:#010x}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("--readelf", required=True, type=Path)
    parser.add_argument("--objdump", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--objects", nargs="+", required=True, type=Path)
    args = parser.parse_args()

    try:
        header = run(args.readelf, "--file-header", str(args.elf))
        program_headers = run(args.readelf, "--program-headers", str(args.elf))
        symbols_text = run(args.readelf, "--symbols", str(args.elf))
        disassembly = run(args.objdump, "--disassemble", str(args.elf))
        symbols, undefined = parse_symbols(symbols_text)

        if re.search(r"Machine:\s+(?:Propeller|12c\b)", header) is None:
            raise RuntimeError("ELF machine is not Propeller")
        if re.search(r"Entry point address:\s+0x0\b", header) is None:
            raise RuntimeError("ELF entry point is not Hub address zero")
        if first_load_paddr(program_headers) != 0:
            raise RuntimeError("first PT_LOAD physical address is not zero")

        required = {
            "__entry",
            "__start0",
            "__start",
            "main",
            "__stack",
            "_bss_end",
            "p2_context_int1",
            "p2_context_dispatch",
            "p2_context_start",
            "p2_context_timer_enable",
            "p2_context_timer_mask",
            "p2_context_timer_quiesce",
            "p2_context_register_window",
            "g_p2_context_irq_area",
            "g_p2_context_irq_stack",
            "__mulsi3",
            "__udivdi3",
        }
        missing = sorted(required.difference(symbols))
        if missing:
            raise RuntimeError("missing symbols: " + ", ".join(missing))
        if symbols["__entry"] != 0:
            raise RuntimeError("__entry is not Hub address zero")
        if symbols["main"] < 0x0A00:
            raise RuntimeError("normal Hub C main is below 0x0a00")
        if symbols["_bss_end"] > symbols["__stack"]:
            raise RuntimeError("standalone data overlaps boot stack")
        if (symbols["g_p2_context_irq_area"] & 0x1FF) != 0:
            raise RuntimeError("fixed IRQ scratch lost 512-byte alignment")
        if (symbols["g_p2_context_irq_stack"] & 0x1FF) != 0:
            raise RuntimeError("dedicated IRQ stack lost 512-byte alignment")
        if undefined:
            raise RuntimeError("undefined symbols: " + ", ".join(undefined))

        forbidden_q = re.findall(
            r"\b(?:qmul|qdiv|getqx|getqy)\b", disassembly, re.IGNORECASE
        )
        if forbidden_q:
            raise RuntimeError("compiler CORDIC instructions remain in ELF")

        for obj in args.objects:
            relocations = run(args.readelf, "--relocations", str(obj))
            if "R_P2_COG9" in relocations:
                raise RuntimeError(f"COG9 relocation remains in {obj}")

        assembly = args.assembly.read_text(encoding="utf-8")
        source = args.source.read_text(encoding="utf-8")
        verify_assembly(assembly)
        verify_setq_aug_block_pairs(assembly)
        verify_startup_source(source)
        verify_linked_isr(disassembly)
        verify_dispatch_hotpath(disassembly)
        verify_outgoing_stack_args(source, disassembly)
        for obj in args.objects:
            if obj.name == "context_switch.o":
                object_disassembly = run(
                    args.objdump, "--disassemble", "--reloc", str(obj)
                )
                verify_symbolic_aug_pairs(object_disassembly)
        verify_raw_words(args.binary.read_bytes(), symbols)
        verify_absolute_scratch_addresses(args.binary.read_bytes(), symbols)

    except (OSError, RuntimeError, ValueError) as error:
        print(f"VERIFY FAILED: {error}", file=sys.stderr)
        return 1

    print(
        "VERIFIED: native P2 CT1 ELF; entry/load=0; 37+1 frame; "
        "detached guarded IRQ scratch/stack; pre-STALLI GETBRK WCZ; "
        "outgoing stack-arg hazard retained; exact raw controls; Q-free; "
        "no COG9 or undefined symbols"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
