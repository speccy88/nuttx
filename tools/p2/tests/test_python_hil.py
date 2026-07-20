#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import binascii
import contextlib
import fcntl
import importlib.util
import io
import json
import os
import pathlib
import re
import shlex
import subprocess
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).parents[1] / "test-python.py"
PYTHON_DEFCONFIG = (
    pathlib.Path(__file__).parents[3]
    / "boards/p2/p2x8c4m64p/p2-ec32mb/configs/python/defconfig"
)
SPEC = importlib.util.spec_from_file_location("p2_python_hil", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
hil = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hil
SPEC.loader.exec_module(hil)


class FakeSerial:
    def __init__(self, incoming, read_size=3):
        self.incoming = bytearray(incoming)
        self.outgoing = bytearray()
        self.read_size = read_size
        self.flushes = 0
        self.closes = 0
        self.write_timeout = 10.0
        self.write_timeouts = []

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, size):
        count = min(size, self.read_size, len(self.incoming))
        result = bytes(self.incoming[:count])
        del self.incoming[:count]
        return result

    def write(self, data):
        self.write_timeouts.append(self.write_timeout)
        count = min(7, len(data))
        self.outgoing.extend(data[:count])
        return count

    def flush(self):
        self.flushes += 1

    def close(self):
        self.closes += 1


def overlay_stats_line(
    stage,
    *,
    entries=0,
    exits=0,
    direct=0,
    attempts=0,
    loads=0,
    load_bytes=0,
    depth=0,
    maximum=0,
    group=0,
    loading_group=0,
    loading_bytes=0,
    requested_group=0,
    stub=0xFFFFFFFF,
    flags=1,
    error=0,
):
    return (
        "P2PY:OVL:{}:E={:016X}:X={:016X}:D={:016X}:A={:016X}:"
        "L={:016X}:B={:016X}:DEP={:08X}:MAX={:08X}:G={:08X}:"
        "LG={:08X}:LB={:08X}:REQ={:08X}:STUB={:08X}:F={:02X}:"
        "ERR={}\r\n"
    ).format(
        stage,
        entries,
        exits,
        direct,
        attempts,
        loads,
        load_bytes,
        depth,
        maximum,
        group,
        loading_group,
        loading_bytes,
        requested_group,
        stub,
        flags,
        error,
    ).encode("ascii")


def xmem_stats_line(
    stage, *, hits=0, misses=0, fills=0, writes=0, bypasses=0
):
    return (
        "P2PY:XMEM:{}:H={:016X}:M={:016X}:F={:016X}:W={:016X}:"
        "B={:016X}\r\n"
    ).format(stage, hits, misses, fills, writes, bypasses).encode("ascii")


WORKER_EXIT_LINE = b"P2PY:WORKER:EXIT:CODE=0\r\n"
WORKER_STACK_LINE = b"P2PY:WORKER:STACK:FREE=4096:SIZE=24576\r\n"
ENTROPY_FINGERPRINT = "0123456789abcdef0123456789abcdef"
SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS = hil.SOFTFLOAT_PROBE_FILL_TIME_CALLS


def initialization_diagnostics(type_count=hil.PYTHON_STATIC_TYPE_COUNT):
    lines = [
        b"P2PY:INIT:GIL:TSTATE:PASS\r\n",
        b"P2PY:INIT:GIL:READY:PASS\r\n",
        b"P2PY:INIT:GLOBAL_OBJECTS:BEGIN\r\n",
        b"P2PY:INIT:UNICODE_STATIC:BEGIN\r\n",
        b"P2PY:INIT:UNICODE_STATIC:PASS\r\n",
        b"P2PY:INIT:LATIN1:BEGIN\r\n",
        b"P2PY:INIT:LATIN1:PASS\r\n",
        b"P2PY:INIT:GLOBAL_OBJECTS:PASS\r\n",
        b"P2PY:INIT:CODE:BEGIN\r\n",
        b"P2PY:INIT:CODE:PASS\r\n",
        b"P2PY:INIT:DTOA:BEGIN\r\n",
        b"P2PY:INIT:DTOA:PASS\r\n",
        b"P2PY:INIT:GC:BEGIN\r\n",
        b"P2PY:INIT:GC:PASS\r\n",
        b"P2PY:INIT:PYCORE_TYPES:BEGIN\r\n",
        "P2PY:INIT:TYPES:BEGIN:N={}\r\n".format(type_count).encode("ascii"),
    ]
    for index in range(type_count):
        lines.extend(
            (
                "P2PY:INIT:TYPE:I={}:BEFORE\r\n".format(index).encode(
                    "ascii"
                ),
                "P2PY:INIT:TYPE:I={}:AFTER:R=0\r\n".format(index).encode(
                    "ascii"
                ),
            )
        )
    lines.extend(
        (
            "P2PY:INIT:TYPES:PASS:N={}\r\n".format(type_count).encode(
                "ascii"
            ),
            b"P2PY:INIT:PYCORE_TYPES:PASS\r\n",
        )
    )
    return b"".join(lines)


def fill_time_diagnostics(call_count=SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS):
    lines = []
    for index in range(call_count):
        lines.append(
            (
                "P2PY:FILLTIME:RAW:SECLO={:08X}:SECHI={:08X}:NSEC={:08X}\r\n"
            ).format(
                0x10203040 + index,
                0,
                123456789 + index,
            ).encode("ascii")
        )
        lines.extend(marker + b"\r\n" for marker in hil.FILL_TIME_SUCCESS_MARKERS)
    return b"".join(lines)


def startup_diagnostics():
    return b"".join(
        (
            initialization_diagnostics(),
            hil.IMPORTLIB_PASS_MARKER + b"\r\n",
            hil.PATHCONFIG_BEGIN_MARKER + b"\r\n",
            hil.PATHCONFIG_PASS_MARKER + b"\r\n",
            hil.MAIN_PASS_MARKER + b"\r\n",
        )
    )


def softfloat_probe_diagnostics():
    return (
        hil.SOFTFLOAT_PROBE_BEGIN_MARKER.encode("ascii")
        + b"\r\n"
        + fill_time_diagnostics()
        + hil.SOFTFLOAT_PROBE_PASS_MARKER.encode("ascii")
        + b"\r\n"
    )


def qualified_serial(
    lifecycle_count=None,
    *,
    plan=hil.FULL_QUALIFICATION_PLAN,
    include_sample=True,
    race=None,
    prompt_prefix=False,
    interactive_index=None,
    sample_indices=None,
):
    """Build deterministic, internally consistent full-run serial evidence."""

    if lifecycle_count is None:
        lifecycle_count = len(plan.expected_worker_names)
    if interactive_index is None:
        interactive_index = plan.expected_worker_names.index(
            hil.INTERACTIVE_REPL_TEST_NAME
        )
    if sample_indices is None:
        sample_indices = (0,) if include_sample else ()
    sample_indices = set(sample_indices)

    races = (
        "launch_before_begin",
        "launch_between_begin_end",
        "launch_after_end",
    )
    entries = 0
    exits = 0
    attempts = 0
    loads = 0
    load_bytes = 0
    maximum = 0
    group = 0
    requested_group = 0
    stub = 0xFFFFFFFF
    lines = []

    for index in range(lifecycle_count):
        selected_race = race if race is not None else races[index % len(races)]
        sample_this_lifecycle = index in sample_indices
        target_group = index % 7 + 1
        target_stub = index + 1
        base = {
            "entries": entries,
            "exits": exits,
            "attempts": attempts,
            "loads": loads,
            "load_bytes": load_bytes,
            "maximum": maximum,
            "group": group,
            "requested_group": requested_group,
            "stub": stub,
        }
        active = {
            "entries": entries + 1,
            "exits": exits,
            "attempts": attempts + 1,
            "loads": loads + 1,
            "load_bytes": load_bytes + 0x1000,
            "depth": 1,
            "maximum": max(maximum, 1),
            "group": target_group,
            "requested_group": target_group,
            "stub": target_stub,
        }
        complete = {
            **active,
            "exits": exits + 1,
            "depth": 0,
        }

        begin = overlay_stats_line("BEGIN", **base)
        end = overlay_stats_line("END", **complete)
        final = overlay_stats_line("FINAL", **complete)
        active_sample = overlay_stats_line("SAMPLE", **active)
        idle_sample = overlay_stats_line("SAMPLE", **complete)
        early_launch = overlay_stats_line("LAUNCH", **base)
        late_launch = overlay_stats_line("LAUNCH", **complete)
        startup_body = startup_diagnostics()
        worker_test_body = b""
        if index < len(plan.python_workers):
            if any(
                test.name == hil.SOFTFLOAT_PROBE_TEST_NAME
                for test in plan.python_workers[index].tests
            ):
                worker_test_body += softfloat_probe_diagnostics()
            for test in plan.python_workers[index].tests:
                if test.name == "hardware_entropy":
                    worker_test_body += (
                        hil.ENTROPY_FINGERPRINT_PREFIX
                        + ENTROPY_FINGERPRINT
                        + "\r\n"
                    ).encode("ascii")
                worker_test_body += test.marker.encode("ascii") + b"\r\n"

        worker_name = (
            plan.expected_worker_names[index]
            if index < len(plan.expected_worker_names)
            else None
        )
        interactive_body = b""
        if index == interactive_index:
            interactive_body = b"Python 3.13.0 test banner\r\n"
            for command in hil.persistent_repl_setup_commands(
                plan.full_qualification
            ):
                interactive_body += (
                    hil.INTERACTIVE_REPL_PROMPT
                    + command.encode("ascii")
                    + b"\r\n"
                )
            interactive_body += (
                hil.INTERACTIVE_REPL_PROMPT
                + hil.persistent_repl_exec_command().encode("ascii")
                + b"\r\n"
                + hil.INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER.encode("ascii")
                + b"\r\n"
                + worker_test_body
                + hil.INTERACTIVE_REPL_SCRIPT_PASS_MARKER.encode("ascii")
                + b"\r\n"
                + hil.INTERACTIVE_REPL_PROMPT
                + hil.INTERACTIVE_REPL_EXPRESSION_COMMAND.encode("ascii")
                + b"\r\n"
                + hil.INTERACTIVE_REPL_EXPRESSION_MARKER.encode("ascii")
                + b"\r\n"
                + hil.INTERACTIVE_REPL_PROMPT
                + hil.INTERACTIVE_REPL_EXIT_COMMAND.encode("ascii")
                + b"\r\n"
            )
        else:
            startup_body += worker_test_body

        if worker_name is not None and worker_name.startswith("restart_stress_"):
            iteration = int(worker_name.rsplit("_", 1)[1])
            startup_body += (
                "P2PYTEST:RESTART:{}:PASS\r\n".format(iteration)
            ).encode("ascii")
        elif worker_name == hil.CONCURRENCY_HOLDER_TEST_NAME:
            startup_body += (
                hil.CONCURRENCY_HOLDER_MARKER
                + "\r\n"
                + hil.CONCURRENCY_BUSY_PREFIX
                + "16\r\n"
                + hil.CONCURRENCY_DONE_MARKER
                + "\r\n"
            ).encode("ascii")
        elif worker_name == hil.CONCURRENCY_POST_TEST_NAME:
            startup_body += (
                hil.CONCURRENCY_POST_MARKER + "\r\n"
            ).encode("ascii")

        if selected_race == "launch_before_begin":
            lines.extend((early_launch, begin, startup_body))
            if interactive_body:
                lines.append(interactive_body)
            if sample_this_lifecycle:
                lines.append(active_sample)
            lines.append(end)
        elif selected_race == "launch_between_begin_end":
            lines.extend((begin, early_launch, startup_body))
            if interactive_body:
                lines.append(interactive_body)
            if sample_this_lifecycle:
                lines.append(active_sample)
            lines.append(end)
        elif selected_race == "launch_after_end":
            lines.extend((begin, startup_body))
            if interactive_body:
                lines.append(interactive_body)
            lines.extend((end, late_launch))
            if sample_this_lifecycle:
                lines.append(idle_sample)
        else:
            raise AssertionError("unknown fixture race {}".format(selected_race))

        lines.extend((WORKER_EXIT_LINE, WORKER_STACK_LINE, final))
        if selected_race == "launch_before_begin":
            xmem_stages = ["LAUNCH", "BEGIN"]
            if sample_this_lifecycle:
                xmem_stages.append("SAMPLE")
            xmem_stages.extend(("END", "FINAL"))
        elif selected_race == "launch_between_begin_end":
            xmem_stages = ["BEGIN", "LAUNCH"]
            if sample_this_lifecycle:
                xmem_stages.append("SAMPLE")
            xmem_stages.extend(("END", "FINAL"))
        else:
            xmem_stages = ["BEGIN", "END", "LAUNCH"]
            if sample_this_lifecycle:
                xmem_stages.append("SAMPLE")
            xmem_stages.append("FINAL")
        xmem_values = {
            "hits": (index + 1) * 100,
            "misses": (index + 1) * 10,
            "fills": (index + 1) * 9,
            "writes": (index + 1) * 20,
            "bypasses": (index + 1) * 5,
        }
        lines.extend(
            xmem_stats_line(stage, **xmem_values) for stage in xmem_stages
        )
        entries += 1
        exits += 1
        attempts += 1
        loads += 1
        load_bytes += 0x1000
        maximum = 1
        group = target_group
        requested_group = target_group
        stub = target_stub

    if prompt_prefix and lines:
        lines[0] = b"nsh> " + lines[0]
    return b"".join(lines)


def successful_hil_result(plan=hil.FULL_QUALIFICATION_PLAN):
    def sample(name):
        return {"test": name, "free": 4096, "size": 24576, "used": 20480}

    stacks = [sample(name) for name in plan.expected_worker_names]
    interactive_index = plan.expected_worker_names.index(
        hil.INTERACTIVE_REPL_TEST_NAME
    )
    interactive = stacks[interactive_index]
    holder_index = plan.expected_worker_names.index(
        hil.CONCURRENCY_HOLDER_TEST_NAME
    )
    post_index = plan.expected_worker_names.index(hil.CONCURRENCY_POST_TEST_NAME)
    concurrency = [stacks[holder_index], stacks[post_index]]
    setup_commands = hil.persistent_repl_setup_commands(
        plan.full_qualification
    )
    if plan.include_restart_stress:
        restart = [
            sample("restart_stress_{}".format(index))
            for index in range(hil.RESTART_STRESS_COUNT)
        ]
        restart_result = {
            "count": hil.RESTART_STRESS_COUNT,
            "stack_samples": restart,
        }
    else:
        restart_result = {
            "skipped": True,
            "reason": hil.FULL_RESTART_SKIP_REASON,
        }
    return {
        "completed_tests": list(plan.completed_test_names),
        "stack_samples": stacks,
        "interactive_repl": {
            "banner": "Python 3.13.0 test banner",
            "prompt": hil.INTERACTIVE_REPL_PROMPT.decode("ascii"),
            "expression_marker": hil.INTERACTIVE_REPL_EXPRESSION_MARKER,
            "setup": {
                "command_count": len(setup_commands),
                "command_bytes": sum(
                    len((command + "\r").encode("ascii"))
                    for command in setup_commands
                ),
                "prompt_ack_count": len(setup_commands),
                "maximum_command_bytes": max(
                    len((command + "\r").encode("ascii"))
                    for command in setup_commands
                ),
            },
            "execution_command": hil.persistent_repl_exec_command(),
            "script_path": hil.QUALIFICATION_BATCH_PATH,
            "script_tests": [
                test.name for test in plan.python_workers[interactive_index].tests
            ],
            "script_begin_marker": hil.INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER,
            "script_pass_marker": hil.INTERACTIVE_REPL_SCRIPT_PASS_MARKER,
            "exit_command": hil.INTERACTIVE_REPL_EXIT_COMMAND,
            "exit_code": 0,
            "stack_sample": interactive,
        },
        "restart_stress": restart_result,
        "concurrency": {
            "holder_marker": hil.CONCURRENCY_HOLDER_MARKER,
            "busy_marker": hil.CONCURRENCY_BUSY_PREFIX + "16",
            "done_marker": hil.CONCURRENCY_DONE_MARKER,
            "post_marker": hil.CONCURRENCY_POST_MARKER,
            "stack_samples": concurrency,
        },
        "minimum_stack_free": 4096,
        "entropy_fingerprint": ENTROPY_FINGERPRINT,
    }


def successful_smoke_result():
    setup_commands = hil.persistent_repl_setup_commands(False)
    interactive = {
        "test": hil.INTERACTIVE_REPL_TEST_NAME,
        "free": 4096,
        "size": 24576,
        "used": 20480,
    }
    return {
        "completed_tests": ["arithmetic", hil.INTERACTIVE_REPL_TEST_NAME],
        "stack_samples": [interactive],
        "interactive_repl": {
            "banner": "Python 3.13.0 test banner",
            "prompt": hil.INTERACTIVE_REPL_PROMPT.decode("ascii"),
            "expression_marker": hil.INTERACTIVE_REPL_EXPRESSION_MARKER,
            "setup": {
                "command_count": len(setup_commands),
                "command_bytes": sum(
                    len((command + "\r").encode("ascii"))
                    for command in setup_commands
                ),
                "prompt_ack_count": len(setup_commands),
                "maximum_command_bytes": max(
                    len((command + "\r").encode("ascii"))
                    for command in setup_commands
                ),
            },
            "execution_command": hil.persistent_repl_exec_command(),
            "script_path": hil.QUALIFICATION_BATCH_PATH,
            "script_tests": ["arithmetic"],
            "script_begin_marker": hil.INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER,
            "script_pass_marker": hil.INTERACTIVE_REPL_SCRIPT_PASS_MARKER,
            "exit_command": hil.INTERACTIVE_REPL_EXIT_COMMAND,
            "exit_code": 0,
            "stack_sample": interactive,
            "live_hold": {
                "requested_seconds": hil.SMOKE_REPL_LIVE_HOLD_SECONDS,
                "elapsed_seconds": hil.SMOKE_REPL_LIVE_HOLD_SECONDS,
                "sample_marker": overlay_stats_line("SAMPLE").decode(
                    "ascii"
                ).rstrip("\r\n"),
            },
        },
        "restart_stress": {
            "skipped": True,
            "reason": hil.SMOKE_SKIP_REASON,
        },
        "concurrency": {
            "skipped": True,
            "reason": hil.SMOKE_SKIP_REASON,
        },
        "minimum_stack_free": 4096,
    }


class PythonHilProtocolTests(unittest.TestCase):
    def test_smoke_hold_spans_the_configured_telemetry_period(self):
        config = PYTHON_DEFCONFIG.read_text(encoding="utf-8")
        match = re.search(
            r"^CONFIG_INTERPRETERS_CPYTHON_P2_OVERLAY_TELEMETRY_INTERVAL_MS="
            r"(\d+)$",
            config,
            re.MULTILINE,
        )
        self.assertIsNotNone(match)
        configured_seconds = int(match.group(1)) / 1000.0

        self.assertEqual(
            hil.OVERLAY_TELEMETRY_INTERVAL_SECONDS,
            configured_seconds,
        )
        self.assertEqual(hil.SMOKE_REPL_LIVE_HOLD_MARGIN_SECONDS, 5.0)
        self.assertEqual(
            hil.SMOKE_REPL_LIVE_HOLD_SECONDS,
            configured_seconds + 5.0,
        )
        self.assertGreater(
            hil.SMOKE_REPL_LIVE_HOLD_SECONDS,
            configured_seconds,
        )

    def test_qualification_plans_keep_full_default_and_bound_smoke(self):
        full = hil.qualification_plan("full")
        smoke = hil.qualification_plan("smoke")
        overnight = hil.qualification_plan("overnight")

        self.assertIs(full, hil.FULL_QUALIFICATION_PLAN)
        self.assertEqual(len(full.python_tests), len(hil.PYTHON_TESTS))
        self.assertEqual(
            full.expected_worker_names,
            hil.EXPECTED_SUCCESSFUL_WORKER_NAMES,
        )
        self.assertTrue(full.full_qualification)
        self.assertEqual(full.success_status, "PASS")
        self.assertEqual(
            [worker.name for worker in full.python_workers],
            [
                hil.INTERACTIVE_REPL_TEST_NAME,
                hil.CONCURRENCY_HOLDER_TEST_NAME,
                hil.CONCURRENCY_POST_TEST_NAME,
            ],
        )
        self.assertEqual(len(full.python_workers), 3)
        self.assertEqual(len(full.expected_worker_names), 3)
        self.assertEqual(
            [worker.command for worker in full.python_workers],
            [
                "python",
                "python -E /tmp/p2e.py &",
                "python -I /tmp/p2i.py",
            ],
        )
        self.assertFalse(full.include_restart_stress)
        self.assertEqual(
            len(overnight.expected_worker_names),
            3 + hil.RESTART_STRESS_COUNT,
        )
        self.assertTrue(overnight.include_restart_stress)
        self.assertTrue(overnight.full_qualification)
        self.assertEqual(
            overnight.expected_worker_names[:3], full.expected_worker_names
        )

        self.assertEqual([test.name for test in smoke.python_tests], ["arithmetic"])
        self.assertEqual(
            smoke.expected_worker_names,
            (hil.INTERACTIVE_REPL_TEST_NAME,),
        )
        self.assertEqual(len(smoke.omitted_test_names), len(hil.PYTHON_TESTS) - 1)
        self.assertFalse(smoke.include_restart_stress)
        self.assertFalse(smoke.include_concurrency)
        self.assertFalse(smoke.full_qualification)
        self.assertEqual(smoke.success_status, "SMOKE_PASS")
        self.assertEqual(
            smoke.repl_live_hold_seconds,
            hil.SMOKE_REPL_LIVE_HOLD_SECONDS,
        )

    def test_full_repl_script_is_marker_safe_and_assigns_every_test_once(self):
        hil.validate_test_commands()
        batch = hil.FULL_QUALIFICATION_PLAN.python_workers[0]
        self.assertEqual(batch.name, hil.INTERACTIVE_REPL_TEST_NAME)
        self.assertEqual(len(batch.tests), 23)
        self.assertEqual(batch.tests[-1].name, "final")
        self.assertEqual(
            {test.name for test in batch.tests},
            {test.name for test in hil.PYTHON_TESTS}
            - set(hil.SPECIAL_RESTART_TEST_NAMES),
        )
        assigned = [
            test.name
            for worker in hil.FULL_QUALIFICATION_PLAN.python_workers
            for test in worker.tests
        ]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(set(assigned), {test.name for test in hil.PYTHON_TESTS})
        compile(
            "\n".join(hil.QUALIFICATION_BATCH_SCRIPT) + "\n",
            hil.QUALIFICATION_BATCH_PATH,
            "exec",
        )
        self.assertFalse(batch.setup_commands)
        self.assertTrue(
            all("'" not in line for line in hil.QUALIFICATION_BATCH_SCRIPT)
        )
        commands = hil.persistent_repl_setup_commands(True)
        self.assertGreater(len(commands), 3)
        self.assertTrue(
            all(
                len((command + "\r").encode("ascii")) <= hil.LINE_MAX
                for command in commands
            )
        )
        for command in commands + (hil.persistent_repl_exec_command(),):
            self.assertNotIn(hil.INTERACTIVE_REPL_PROMPT.decode("ascii"), command)
            for test in hil.PYTHON_TESTS:
                self.assertNotIn(test.marker, command)
        oversized = "x" * hil.LINE_MAX
        fake = FakeSerial(b"")
        with self.assertRaisesRegex(
            hil.PythonHilError, "exceeds the console ABI"
        ):
            hil.write_repl_line(hil.SerialSession(fake), oversized)
        self.assertEqual(bytes(fake.outgoing), b"")

    def test_bounded_repl_setup_reconstructs_every_script_exactly(self):
        class Sink:
            def __init__(self):
                self.parts = []
                self.closed = False

            def write(self, value):
                self.parts.append(value)
                return len(value)

            def close(self):
                self.closed = True

        for full_qualification in (False, True):
            with self.subTest(full_qualification=full_qualification):
                sinks = {}

                def fake_open(path, mode):
                    self.assertEqual(mode, "w")
                    sink = Sink()
                    sinks[path] = sink
                    return sink

                namespace = {"open": fake_open}
                commands = hil.persistent_repl_setup_commands(
                    full_qualification
                )
                for command in commands:
                    exec(command, namespace)
                expected = dict(
                    hil.persistent_repl_scripts(full_qualification)
                )
                self.assertEqual(set(sinks), set(expected))
                for path, source in expected.items():
                    self.assertTrue(sinks[path].closed)
                    self.assertEqual("".join(sinks[path].parts), source)

    def test_raw_markers_cannot_be_credited_across_worker_groups(self):
        base = qualified_serial()
        arithmetic = b"P2PYTEST:ARITH:PASS\r\n"
        float_libm = b"P2PYTEST:FLOAT:PASS\r\n"
        placeholder = b"P2PYTEST:CROSS_GROUP:PLACEHOLDER\r\n"
        crossed = base.replace(arithmetic, placeholder, 1)
        crossed = crossed.replace(float_libm, arithmetic, 1)
        crossed = crossed.replace(placeholder, float_libm, 1)
        telemetry = hil.parse_overlay_telemetry(
            crossed, successful_hil_result()
        )
        self.assertFalse(telemetry["analysis"]["qualification_valid"])
        errors = "\n".join(telemetry["qualification_errors"])
        self.assertIn("worker interactive_repl test markers", errors)

    def test_batch_markers_must_remain_in_exact_test_order(self):
        base = qualified_serial()
        float_libm = b"P2PYTEST:FLOAT:PASS\r\n"
        unicode = b"P2PYTEST:UNICODE:PASS\r\n"
        placeholder = b"P2PYTEST:BATCH_ORDER:PLACEHOLDER\r\n"
        reordered = base.replace(float_libm, placeholder, 1)
        reordered = reordered.replace(unicode, float_libm, 1)
        reordered = reordered.replace(placeholder, unicode, 1)
        telemetry = hil.parse_overlay_telemetry(
            reordered, successful_hil_result()
        )
        self.assertFalse(telemetry["analysis"]["qualification_valid"])
        self.assertIn(
            "worker interactive_repl test markers",
            "\n".join(telemetry["qualification_errors"]),
        )

    def test_entropy_fingerprint_is_raw_bound_and_cross_checked(self):
        fingerprint = (
            hil.ENTROPY_FINGERPRINT_PREFIX
            + ENTROPY_FINGERPRINT
            + "\r\n"
        ).encode("ascii")
        base = qualified_serial()

        missing = hil.parse_overlay_telemetry(
            base.replace(fingerprint, b"", 1), successful_hil_result()
        )
        self.assertFalse(missing["analysis"]["qualification_valid"])
        self.assertIn(
            "entropy fingerprint count 0 != 1",
            "\n".join(missing["qualification_errors"]),
        )

        crossed = base.replace(fingerprint, b"", 1).replace(
            b"P2PYTEST:ARITH:PASS\r\n",
            fingerprint + b"P2PYTEST:ARITH:PASS\r\n",
            1,
        )
        crossed_telemetry = hil.parse_overlay_telemetry(
            crossed, successful_hil_result()
        )
        self.assertFalse(
            crossed_telemetry["analysis"]["qualification_valid"]
        )
        self.assertIn(
            "hardware entropy fingerprint is outside its assigned test interval",
            "\n".join(crossed_telemetry["qualification_errors"]),
        )

        tampered_result = successful_hil_result()
        tampered_result["entropy_fingerprint"] = "f" * 32
        tampered = hil.parse_overlay_telemetry(base, tampered_result)
        self.assertFalse(tampered["analysis"]["qualification_valid"])
        self.assertIn(
            "entropy_fingerprint does not match raw serial evidence",
            tampered["result_validation_errors"],
        )

    def test_restart_markers_are_exact_and_bound_to_each_lifecycle(self):
        plan = hil.OVERNIGHT_QUALIFICATION_PLAN
        base = qualified_serial(plan=plan)
        result = successful_hil_result(plan)
        marker0 = b"P2PYTEST:RESTART:0:PASS\r\n"
        marker1 = b"P2PYTEST:RESTART:1:PASS\r\n"

        missing = hil.parse_overlay_telemetry(
            base.replace(marker0, b"", 1), result, plan
        )
        self.assertFalse(missing["analysis"]["qualification_valid"])
        self.assertIn(
            "restart marker count 19 != 20",
            "\n".join(missing["qualification_errors"]),
        )

        noncanonical = hil.parse_overlay_telemetry(
            base.replace(marker0, b"P2PYTEST:RESTART:00:PASS\r\n", 1),
            result,
            plan,
        )
        self.assertFalse(noncanonical["analysis"]["qualification_valid"])
        self.assertIn(
            "1 malformed restart markers",
            "\n".join(noncanonical["qualification_errors"]),
        )

        placeholder = b"P2PYTEST:RESTART:999:PLACEHOLDER\r\n"
        crossed = base.replace(marker0, placeholder, 1)
        crossed = crossed.replace(marker1, marker0, 1)
        crossed = crossed.replace(placeholder, marker1, 1)
        crossed_telemetry = hil.parse_overlay_telemetry(
            crossed, result, plan
        )
        self.assertFalse(
            crossed_telemetry["analysis"]["qualification_valid"]
        )
        errors = "\n".join(crossed_telemetry["qualification_errors"])
        self.assertIn("restart markers are not the exact ordered", errors)
        self.assertIn(
            "restart_stress_0 marker is outside its assigned lifecycle", errors
        )
        self.assertIn(
            "restart_stress_1 marker is outside its assigned lifecycle", errors
        )

    def test_concurrency_markers_are_ordered_and_lifecycle_bound(self):
        base = qualified_serial()
        holder = (hil.CONCURRENCY_HOLDER_MARKER + "\r\n").encode("ascii")
        done = (hil.CONCURRENCY_DONE_MARKER + "\r\n").encode("ascii")
        post = (hil.CONCURRENCY_POST_MARKER + "\r\n").encode("ascii")

        missing = hil.parse_overlay_telemetry(
            base.replace(done, b"", 1), successful_hil_result()
        )
        self.assertFalse(missing["analysis"]["qualification_valid"])
        self.assertIn(
            "concurrency done marker count 0 != 1",
            "\n".join(missing["qualification_errors"]),
        )

        noncanonical_busy = hil.parse_overlay_telemetry(
            base.replace(
                (hil.CONCURRENCY_BUSY_PREFIX + "16\r\n").encode("ascii"),
                (hil.CONCURRENCY_BUSY_PREFIX + "016\r\n").encode("ascii"),
                1,
            ),
            successful_hil_result(),
        )
        self.assertFalse(
            noncanonical_busy["analysis"]["qualification_valid"]
        )
        self.assertIn(
            "1 malformed concurrency markers",
            "\n".join(noncanonical_busy["qualification_errors"]),
        )

        crossed = base.replace(post, b"", 1).replace(
            done, post + done, 1
        )
        crossed_telemetry = hil.parse_overlay_telemetry(
            crossed, successful_hil_result()
        )
        self.assertFalse(
            crossed_telemetry["analysis"]["qualification_valid"]
        )
        self.assertIn(
            "concurrency post marker is outside its assigned lifecycle",
            "\n".join(crossed_telemetry["qualification_errors"]),
        )

        forbidden = base.replace(
            done,
            (hil.CONCURRENCY_SECOND_MARKER + "\r\n").encode("ascii") + done,
            1,
        )
        forbidden_telemetry = hil.parse_overlay_telemetry(
            forbidden, successful_hil_result()
        )
        self.assertFalse(
            forbidden_telemetry["analysis"]["qualification_valid"]
        )
        self.assertIn(
            "concurrency second_ran marker count 1 != 0",
            "\n".join(forbidden_telemetry["qualification_errors"]),
        )

        tampered_result = successful_hil_result()
        tampered_result["concurrency"]["busy_marker"] = (
            hil.CONCURRENCY_BUSY_PREFIX + "17"
        )
        tampered = hil.parse_overlay_telemetry(base, tampered_result)
        self.assertFalse(tampered["analysis"]["qualification_valid"])
        self.assertIn(
            "concurrency busy_marker does not match raw serial evidence",
            tampered["result_validation_errors"],
        )

    def test_artifact_preflight_verifies_resident_fingerprint_before_hil(self):
        verified = types.SimpleNamespace(
            file_size=4096,
            manifest_sha256=bytes.fromhex("11" * 32),
            build_fingerprint=bytes.fromhex("22" * 32),
            overlay_load_address=0x64000,
            overlay_slot_size=0x18000,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            image = root / "nuttx.bin"
            resident = root / "nuttx"
            container = root / "nuttx.p2py"
            image.write_bytes(b"resident-raw")
            resident.write_bytes(b"resident-elf")
            container.write_bytes(bytes(4096))

            with mock.patch.object(
                hil.p2_python_container,
                "verify_container",
                return_value=verified,
            ):
                with mock.patch.object(
                    hil.p2_python_package, "verify_resident_elf"
                ) as verify_elf:
                    result = hil.validate_artifacts(image, resident, container)
            verify_elf.assert_called_once_with(resident, verified.build_fingerprint)
            self.assertEqual(result["resident_elf_size"], len(b"resident-elf"))
            self.assertEqual(result["container_fingerprint"], "22" * 32)

            with mock.patch.object(
                hil.p2_python_container,
                "verify_container",
                return_value=verified,
            ):
                with mock.patch.object(
                    hil.p2_python_package,
                    "verify_resident_elf",
                    side_effect=hil.p2_python_package.PackageError("mismatch"),
                ):
                    with self.assertRaisesRegex(
                        hil.PythonHilError, "does not match"
                    ):
                        hil.validate_artifacts(image, resident, container)

    def test_upload_preamble_is_fixed_little_endian_abi(self):
        raw = hil.upload_preamble(4096, 0x12345678)
        self.assertEqual(len(raw), 24)
        self.assertEqual(
            hil.UPLOAD_HEADER.unpack(raw),
            (hil.UPLOAD_MAGIC, 3, 24, 4096, 0x12345678, 0),
        )

    def test_upload_preamble_rejects_outside_backing_window(self):
        for size in (0, 191, hil.CONTAINER_CAPACITY + 1):
            with self.subTest(size=size):
                with self.assertRaises(hil.PythonHilError):
                    hil.upload_preamble(size, 0)

    def test_upload_frames_are_sequenced_and_individually_checked(self):
        payload = bytes(
            (index * 29 + 7) & 0xFF
            for index in range(2 * hil.UPLOAD_FRAME_SIZE + 2500)
        )
        frames = list(hil.upload_frames(io.BytesIO(payload), len(payload)))
        self.assertEqual(
            [committed for committed, _ in frames],
            [
                hil.UPLOAD_FRAME_SIZE,
                2 * hil.UPLOAD_FRAME_SIZE,
                len(payload),
            ],
        )
        rebuilt = bytearray()
        expected_offset = 0
        for committed, raw in frames:
            offset, size, checksum = hil.UPLOAD_FRAME.unpack(
                raw[: hil.UPLOAD_FRAME.size]
            )
            data = raw[hil.UPLOAD_FRAME.size :]
            self.assertEqual(offset, expected_offset)
            self.assertEqual(size, len(data))
            self.assertEqual(checksum, binascii.crc32(data) & 0xFFFFFFFF)
            self.assertEqual(committed, offset + size)
            rebuilt.extend(data)
            expected_offset = committed
        self.assertEqual(bytes(rebuilt), payload)

    def test_upload_frames_reject_source_size_changes(self):
        with self.assertRaises(hil.PythonHilError):
            list(hil.upload_frames(io.BytesIO(b"short"), 100))
        with self.assertRaises(hil.PythonHilError):
            list(hil.upload_frames(io.BytesIO(b"too-long"), 3))

    def test_v3_measured_container_needs_179_acks_and_58_64666s_wire_time(self):
        measured_size = 11_727_184
        final_offset = 0
        frame_count = 0
        for final_offset, _frame in hil.upload_frames(
            io.BytesIO(bytes(measured_size)), measured_size
        ):
            frame_count += 1

        self.assertEqual(final_offset, measured_size)
        self.assertEqual(frame_count, 179)
        wire_bytes = measured_size + frame_count * hil.UPLOAD_FRAME.size
        self.assertEqual(wire_bytes, 11_729_332)
        self.assertAlmostEqual(
            wire_bytes * hil.UART_BITS_PER_BYTE / hil.RUNTIME_BAUD,
            58.64666,
        )

    def test_ready_contract_requires_fixed_base_capacity_and_frame(self):
        good = (
            b"P2PY:UPLOAD:READY:PROTO=3:BASE=10300000:"
            b"MAX=13631488:FRAME=65536:BAUD=2000000"
        )
        self.assertEqual(
            hil.parse_ready(good),
            (3, 0x10300000, 13 * 1024 * 1024, 65536, 2000000),
        )
        for bad in (
            good.replace(b"PROTO=3", b"PROTO=2"),
            good.replace(b"10300000", b"10200000"),
            good.replace(b"13631488", b"13631487"),
            good.replace(b"FRAME=65536", b"FRAME=1000"),
            good.replace(b"BAUD=2000000", b"BAUD=115200"),
            good.replace(b":BAUD=2000000", b""),
            b"prefix" + good,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(hil.PythonHilError):
                    hil.parse_ready(bad)

    def test_upload_pass_contract_requires_zero_uart_rx_drops(self):
        expected = b"P2PY:UPLOAD:PASS:SIZE=4096:CRC=1234ABCD:RXDROPS=0"
        self.assertEqual(hil.upload_pass_marker(4096, 0x1234ABCD), expected)
        self.assertNotEqual(
            expected.replace(b"RXDROPS=0", b"RXDROPS=1"),
            hil.upload_pass_marker(4096, 0x1234ABCD),
        )

    def test_python_commands_are_bounded_and_echo_safe(self):
        hil.validate_test_commands()
        self.assertEqual(
            [test.name for test in hil.PYTHON_TESTS],
            [
                "arithmetic",
                "float_libm",
                "unicode",
                "codecs",
                "stdlib",
                "zlib_sizes",
                "zlib_incompressible",
                "zlib_streaming",
                "zlib_checksums",
                "hardware_entropy",
                "runtime_paths",
                "user_site_contract",
                "ignore_environment",
                "isolated_mode",
                "allocation_gc",
                "memory_error_recovery",
                "filesystem",
                "filesystem_large",
                "exceptions",
                "tracemalloc_tls",
                "restart_state_seed",
                "restart_state_isolation",
                "deep_recursion",
                "lock_only_thread",
                "subinterpreters_unsupported",
                "final",
            ],
        )
        self.assertEqual(hil.EXPECTED_SUCCESSFUL_WORKERS, 3)
        codec_test = next(
            test for test in hil.PYTHON_TESTS if test.name == "codecs"
        )
        self.assertIn(
            "import codecs as c,encodings as e,encodings.aliases as a,"
            "encodings.utf_8 as u",
            codec_test.command,
        )
        frozen_origin_assertion = (
            'all(x.__spec__.origin=="frozen" for x in(e,a,u))'
        )
        self.assertIn(frozen_origin_assertion, codec_test.command)
        self.assertIn(
            'c.lookup("latin1").name=="iso8859-1"', codec_test.command
        )
        self.assertTrue(
            any(
                "assert " + frozen_origin_assertion in line
                for line in hil.QUALIFICATION_BATCH_SCRIPT
            )
        )
        self.assertEqual(len((codec_test.command + "\r").encode("ascii")), 220)
        self.assertEqual(
            hil.EXPECTED_SUCCESSFUL_WORKERS,
            len(hil.FULL_QUALIFICATION_PLAN.python_workers),
        )
        self.assertEqual(
            hil.EXPECTED_SUCCESSFUL_WORKER_NAMES[0],
            hil.INTERACTIVE_REPL_TEST_NAME,
        )
        self.assertEqual(
            hil.EXPECTED_SUCCESSFUL_WORKER_NAMES[-2:],
            (
                hil.CONCURRENCY_HOLDER_TEST_NAME,
                hil.CONCURRENCY_POST_TEST_NAME,
            ),
        )
        for test in hil.PYTHON_TESTS:
            self.assertLessEqual(len((test.command + "\r").encode("ascii")), 256)
            self.assertLessEqual(
                len((test.command + "\r").encode("ascii")),
                hil.MAX_UART_WRITE,
            )
            self.assertNotIn(test.marker, test.command)
            for command in test.setup_commands:
                self.assertLessEqual(
                    len((command + "\r").encode("ascii")), 256
                )
                self.assertLessEqual(
                    len((command + "\r").encode("ascii")),
                    hil.MAX_UART_WRITE,
                )
                self.assertNotIn(test.marker, command)

        arithmetic = next(
            test for test in hil.PYTHON_TESTS if test.name == "arithmetic"
        )
        self.assertIn("assert sys.flags.no_site==1", arithmetic.command)
        self.assertIn('"SOFTFLOAT:BEGIN",flush=True', arithmetic.command)
        self.assertIn('os.stat("/tmp")', arithmetic.command)
        self.assertIn("isinstance(s.st_mtime,float)", arithmetic.command)
        self.assertNotIn(hil.SOFTFLOAT_PROBE_BEGIN_MARKER, arithmetic.command)
        self.assertNotIn(hil.SOFTFLOAT_PROBE_PASS_MARKER, arithmetic.command)

        entropy = next(
            test for test in hil.PYTHON_TESTS
            if test.name == "hardware_entropy"
        )
        self.assertIn("os.urandom(256)", entropy.command)
        self.assertIn("secrets.token_bytes(256)", entropy.command)
        self.assertNotIn(hil.ENTROPY_FINGERPRINT_PREFIX, entropy.command)

        zlib_commands = {
            test.name: test.command
            for test in hil.PYTHON_TESTS
            if test.name.startswith("zlib_")
        }
        self.assertIn("b\"\"", zlib_commands["zlib_sizes"])
        self.assertIn("*12000", zlib_commands["zlib_sizes"])
        self.assertIn("randbytes(40000)", zlib_commands["zlib_incompressible"])
        self.assertIn("compressobj()", zlib_commands["zlib_streaming"])
        self.assertIn("decompressobj()", zlib_commands["zlib_streaming"])
        self.assertIn("adler32", zlib_commands["zlib_checksums"])
        self.assertIn("crc32", zlib_commands["zlib_checksums"])

        user_site = next(
            test for test in hil.PYTHON_TESTS
            if test.name == "user_site_contract"
        )
        for contract in (
            "sys.flags.no_site==1",
            '"site" not in sys.modules',
            "site.ENABLE_USER_SITE is None",
            "site.main()",
            "site.ENABLE_USER_SITE is False",
            'os.path.expanduser("~")=="/tmp"',
        ):
            with self.subTest(user_site_contract=contract):
                self.assertIn(contract, user_site.command)

        user_site_source = "\n".join(
            hil.QUALIFICATION_BATCH_SOURCE_OVERRIDES["user_site_contract"]
        ) + "\n"
        compile(user_site_source, "<user_site_contract>", "exec")
        self.assertLess(
            user_site_source.index('assert "site" not in sys.modules'),
            user_site_source.index("import site"),
        )
        self.assertLess(
            user_site_source.index("site.ENABLE_USER_SITE is None"),
            user_site_source.index("site.main()"),
        )
        self.assertLess(
            user_site_source.index("site.main()"),
            user_site_source.index("site.ENABLE_USER_SITE is False"),
        )
        self.assertTrue(
            all(
                len((command + "\r").encode("ascii")) <= hil.LINE_MAX
                for command in hil.persistent_repl_setup_commands(True)
            )
        )

        memory = next(
            test
            for test in hil.PYTHON_TESTS
            if test.name == "memory_error_recovery"
        )
        self.assertIn("except MemoryError", memory.command)
        self.assertIn("while len(x)<63", memory.command)
        self.assertIn("32<=n<63", memory.command)
        self.assertIn("bytearray(1<<18)", memory.command)
        self.assertIn("gc.collect()", memory.command)
        self.assertIn("bytearray(1<<20)", memory.command)
        self.assertIn("y[0]=7;assert y[0]==7", memory.command)
        memory_argv = shlex.split(memory.command)
        self.assertEqual(memory_argv[:2], ["python", "-c"])
        self.assertEqual(len(memory_argv), 3)
        compile(memory_argv[2], "<memory_error_recovery>", "exec")

        for command in (
            hil.INTERACTIVE_REPL_START_COMMAND,
            hil.INTERACTIVE_REPL_EXPRESSION_COMMAND,
            hil.INTERACTIVE_REPL_EXIT_COMMAND,
        ):
            self.assertLessEqual(
                len((command + "\r").encode("ascii")),
                hil.MAX_UART_WRITE,
            )
        self.assertNotIn(
            hil.INTERACTIVE_REPL_EXPRESSION_MARKER,
            hil.INTERACTIVE_REPL_EXPRESSION_COMMAND,
        )

        for marker in (
            hil.CONCURRENCY_HOLDER_MARKER,
            hil.CONCURRENCY_DONE_MARKER,
            hil.CONCURRENCY_SECOND_MARKER,
            hil.CONCURRENCY_POST_MARKER,
        ):
            self.assertNotIn(marker, hil.CONCURRENCY_HOLDER_COMMAND)
            self.assertNotIn(marker, hil.CONCURRENCY_SECOND_COMMAND)

        self.assertEqual(
            hil.RUNTIME_STAGES,
            (
                b"P2PY:TMPFS:READY:PATH=/tmp:HEAP=1048576",
                b"P2PY:ROMDISK:READY:MODE=BUFFERED:SECTOR=512",
                b"P2PY:ROMFS:MOUNTED",
                b"P2PY:CPYTHON:EARLY:START",
                b"P2PY:CPYTHON:EARLY:PASS",
                b"P2PY:CPYTHON:RUN",
            ),
        )

    def test_lock_only_thread_is_one_combined_single_worker_check(self):
        test = next(
            test for test in hil.PYTHON_TESTS
            if test.name == "lock_only_thread"
        )
        self.assertEqual(test.marker, "P2PYTEST:LOCK_ONLY:PASS")
        self.assertEqual(test.command, "python " + hil.LOCK_ONLY_TEST_PATH)
        self.assertGreater(len(test.setup_commands), 0)
        self.assertTrue(
            all(not command.startswith("python ")
                for command in test.setup_commands)
        )
        self.assertEqual(
            tuple(test.setup_commands), hil.LOCK_ONLY_TEST_SETUP_COMMANDS
        )

        source = "\n".join(hil.LOCK_ONLY_TEST_SCRIPT) + "\n"
        compile(source, hil.LOCK_ONLY_TEST_PATH, "exec")
        for contract in (
            '_imp.is_builtin("_thread")==0',
            't.__spec__.origin=="/usr/local/lib/python313.zip/_thread.pyc"',
            "b._thread is None",
            'type(m).__name__=="_DummyModuleLock"',
            "type(lock) is t.LockType",
            "not lock.acquire(False)",
            "rlock._recursion_count()==2",
            "ident==t.get_ident()",
            "start_new_thread",
            "start_new,(callback,())",
            "start_joinable_thread",
            "assert not called",
            "functools,_pyio,_strptime,reprlib,tempfile",
            "_pyio.BufferedReader(raw).read()",
            "_strptime._strptime_time",
            "reprlib.recursive_repr",
            "tempfile.gettempdir()",
            'expect(ImportError,lambda:__import__("threading"))==TE',
            "threading is unavailable: this NuttX P2 profile supports one ",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, source)
        self.assertNotIn(test.marker, source)
        self.assertIn('print("P2PY"+"TEST:LOCK_ONLY:PASS")', source)

    def test_nsh_setup_fails_closed_on_missing_or_failed_commands(self):
        for output in (
            b"mkdir /tmp\r\nnsh: mkdir: command not found\r\nnsh> ",
            b"mount -t tmpfs /tmp\r\nnsh: mount: mount failed: 19\r\nnsh> ",
            b"ERROR: setup broke\r\nnsh> ",
        ):
            with self.subTest(output=output):
                with self.assertRaises(hil.PythonHilError):
                    hil.run_nsh_setup(
                        hil.SerialSession(FakeSerial(output)), b"setup", 1.0
                    )

        session = hil.SerialSession(FakeSerial(b"mkdir /tmp\r\nnsh> "))
        output = hil.run_nsh_setup(session, b"mkdir /tmp", 1.0)
        self.assertIn("mkdir /tmp", output)

    def test_interactive_repl_requires_banner_prompt_expression_and_clean_exit(self):
        setup_commands = hil.persistent_repl_setup_commands(False)
        incoming = b"Python 3.13.0 (P2 NuttX)\r\n>>> "
        incoming += b"".join(
            command.encode("ascii") + b"\r\n>>> "
            for command in setup_commands
        )
        incoming += (
            hil.persistent_repl_exec_command().encode("ascii")
            + b"\r\n"
            + hil.INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER.encode("ascii")
            + b"\r\n"
            + softfloat_probe_diagnostics()
            + b"P2PYTEST:ARITH:PASS\r\n"
            + hil.INTERACTIVE_REPL_SCRIPT_PASS_MARKER.encode("ascii")
            + b"\r\n>>> "
            + hil.INTERACTIVE_REPL_EXPRESSION_COMMAND.encode("ascii")
            + b"\r\n"
            + hil.INTERACTIVE_REPL_EXPRESSION_MARKER.encode("ascii")
            + b"\r\n>>> "
            + hil.INTERACTIVE_REPL_EXIT_COMMAND.encode("ascii")
            + b"\r\n"
            + b"P2PY:WORKER:EXIT:CODE=0\r\n"
            + b"P2PY:WORKER:STACK:FREE=8192:SIZE=24576\r\n"
            + b"nsh> "
        )
        fake = FakeSerial(incoming)
        result = hil.run_interactive_repl_test(
            hil.SerialSession(fake), 1.0
        )

        self.assertTrue(result["banner"].startswith("Python 3."))
        self.assertEqual(result["prompt"], ">>> ")
        self.assertEqual(
            result["expression_marker"],
            hil.INTERACTIVE_REPL_EXPRESSION_MARKER,
        )
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(
            result["stack_sample"],
            {
                "test": hil.INTERACTIVE_REPL_TEST_NAME,
                "free": 8192,
                "size": 24576,
                "used": 16384,
            },
        )
        sent = bytes(fake.outgoing)
        self.assertNotIn(b"python\r", sent)
        self.assertIn(hil.QUALIFICATION_BATCH_PATH.encode("ascii"), sent)
        self.assertEqual(result["setup"]["command_count"], len(setup_commands))
        self.assertEqual(result["setup"]["prompt_ack_count"], len(setup_commands))
        self.assertTrue(
            all(
                (command + "\r").encode("ascii") in sent
                for command in setup_commands
            )
        )
        self.assertIn(
            (hil.INTERACTIVE_REPL_EXPRESSION_COMMAND + "\r").encode("ascii"),
            sent,
        )
        self.assertIn(b"raise SystemExit\r", sent)

        failures = (
            b"ERROR: no Python banner\r\n",
            b"Python 3.13.0\r\n>>> Traceback (most recent call last):\r\n",
            (
                b"Python 3.13.0\r\n>>> "
                + hil.INTERACTIVE_REPL_EXPRESSION_MARKER.encode("ascii")
                + b"\r\n>>> P2PY:WORKER:EXIT:CODE=1\r\n"
            ),
        )
        for failure in failures:
            with self.subTest(failure=failure):
                with self.assertRaises(hil.PythonHilError):
                    hil.run_interactive_repl_test(
                        hil.SerialSession(FakeSerial(failure)), 1.0
                    )

    def test_smoke_repl_hold_requires_fresh_sample_and_no_early_exit(self):
        sample = overlay_stats_line("SAMPLE")
        session = hil.SerialSession(FakeSerial(sample, read_size=len(sample)))
        result = hil.hold_interactive_repl_alive(session, 0.01)
        self.assertGreaterEqual(result["elapsed_seconds"], 0.01)
        self.assertTrue(result["sample_marker"].startswith("P2PY:OVL:SAMPLE:"))

        buffered = hil.SerialSession(FakeSerial(b"", read_size=64))
        buffered.pending.extend(sample)
        with self.assertRaisesRegex(hil.PythonHilError, "no fresh overlay SAMPLE"):
            hil.hold_interactive_repl_alive(buffered, 0.01)

        early_exit = hil.SerialSession(
            FakeSerial(WORKER_EXIT_LINE, read_size=len(WORKER_EXIT_LINE))
        )
        with self.assertRaisesRegex(hil.PythonHilError, "exited during"):
            hil.hold_interactive_repl_alive(early_exit, 0.1)

    def test_concurrency_guard_rejects_second_interpreter_and_holder_finishes(self):
        stack = b"P2PY:WORKER:STACK:FREE=8192:SIZE=24576\r\n"
        incoming = (
            b"nsh> "
            + b"P2PYTEST:IGNORE_ENV:PASS\r\n"
            + b"P2PYTEST:STATE_ISOLATION:PASS\r\n"
            + hil.CONCURRENCY_HOLDER_MARKER.encode("ascii")
            + b"\r\n"
            + hil.CONCURRENCY_BUSY_PREFIX.encode("ascii")
            + b"16\r\nnsh> "
            + hil.CONCURRENCY_DONE_MARKER.encode("ascii")
            + b"\r\n"
            + b"P2PY:WORKER:EXIT:CODE=0\r\n"
            + stack
            + b"P2PYTEST:ISOLATED:PASS\r\n"
            + hil.CONCURRENCY_POST_MARKER.encode("ascii")
            + b"\r\n"
            + b"P2PY:WORKER:EXIT:CODE=0\r\n"
            + stack
            + b"nsh> "
        )
        fake = FakeSerial(incoming)
        session = hil.SerialSession(fake)
        result = hil.run_concurrency_test(session, 1.0)

        self.assertEqual(result["holder_marker"], hil.CONCURRENCY_HOLDER_MARKER)
        self.assertEqual(result["busy_marker"], "P2PY:RUNTIME:BUSY:CODE=16")
        self.assertEqual(result["done_marker"], hil.CONCURRENCY_DONE_MARKER)
        self.assertEqual(result["post_marker"], hil.CONCURRENCY_POST_MARKER)
        self.assertEqual(len(result["stack_samples"]), 2)
        sent = bytes(fake.outgoing)
        self.assertIn(hil.CONCURRENCY_HOLDER_COMMAND.encode("ascii"), sent)
        self.assertIn(hil.CONCURRENCY_SECOND_COMMAND.encode("ascii"), sent)
        self.assertIn(hil.CONCURRENCY_POST_COMMAND.encode("ascii"), sent)

    def test_concurrency_guard_rejects_an_early_worker_exit_marker(self):
        incoming = b"nsh> P2PY:WORKER:EXIT:CODE=0\r\n"
        with self.assertRaisesRegex(
            hil.PythonHilError, "ignore_environment Python test failed"
        ):
            hil.run_concurrency_test(
                hil.SerialSession(FakeSerial(incoming)), 1.0
            )

    def test_restart_stress_requires_twenty_clean_finalize_cycles(self):
        incoming = b"".join(
            (
                "P2PYTEST:RESTART:{}:PASS\r\n"
                "P2PY:WORKER:EXIT:CODE=0\r\n"
                "P2PY:WORKER:STACK:FREE=8192:SIZE=24576\r\n"
                "nsh> "
            ).format(iteration).encode("ascii")
            for iteration in range(hil.RESTART_STRESS_COUNT)
        )
        fake = FakeSerial(incoming)
        result = hil.run_restart_stress(hil.SerialSession(fake), 1.0)
        self.assertEqual(result["count"], 20)
        self.assertEqual(len(result["durations_seconds"]), 20)
        self.assertEqual(len(result["stack_samples"]), 20)
        self.assertEqual(bytes(fake.outgoing).count(b"python -c "), 20)

    def test_worker_exit_must_be_exactly_zero_before_stack_evidence(self):
        good = FakeSerial(b"P2PY:WORKER:EXIT:CODE=0\r\n")
        self.assertEqual(
            hil.wait_worker_exit(hil.SerialSession(good), 1.0), 0
        )

        for line in (
            b"P2PY:WORKER:EXIT:CODE=1\r\n",
            b"P2PY:WORKER:EXIT:CODE=-1\r\n",
            b"P2PY:WORKER:EXIT:CODE=garbage\r\n",
            b"ERROR: worker failed\r\n",
        ):
            with self.subTest(line=line):
                with self.assertRaises(hil.PythonHilError):
                    hil.wait_worker_exit(
                        hil.SerialSession(FakeSerial(line)), 1.0
                    )

    def test_entropy_fingerprint_is_fixed_width_lowercase_hex(self):
        self.assertEqual(
            hil.parse_entropy_fingerprint(
                b"P2PYTEST:ENTROPY:FINGERPRINT:0123456789abcdef0123456789abcdef"
            ),
            "0123456789abcdef0123456789abcdef",
        )
        for line in (
            b"P2PYTEST:ENTROPY:FINGERPRINT:0123",
            b"P2PYTEST:ENTROPY:FINGERPRINT:0123456789ABCDEF0123456789ABCDEF",
            b"P2PYTEST:ENTROPY:FINGERPRINT:gggggggggggggggggggggggggggggggg",
        ):
            with self.subTest(line=line):
                with self.assertRaises(hil.PythonHilError):
                    hil.parse_entropy_fingerprint(line)

    def test_stack_telemetry_is_required_and_enforces_headroom(self):
        good = FakeSerial(b"P2PY:WORKER:STACK:FREE=4096:SIZE=24576\r\n")
        sample = hil.wait_stack_telemetry(hil.SerialSession(good), 1.0)
        self.assertEqual(sample, {"free": 4096, "size": 24576, "used": 20480})

        for line in (
            b"P2PY:WORKER:STACK:FREE=1024:SIZE=24576\r\n",
            b"P2PY:WORKER:STACK:FREE=25000:SIZE=24576\r\n",
            b"P2PY:WORKER:STACK:garbage\r\n",
        ):
            with self.subTest(line=line):
                with self.assertRaises(hil.PythonHilError):
                    hil.wait_stack_telemetry(
                        hil.SerialSession(FakeSerial(line)), 1.0
                    )

    def test_overlay_telemetry_parser_preserves_all_stages_and_deltas(self):
        incoming = (
            b"boot noise\r\n"
            + b"nsh> "
            + overlay_stats_line("LAUNCH")
            + overlay_stats_line(
                "BEGIN",
                entries=1,
                attempts=1,
                loads=1,
                load_bytes=0x1000,
                depth=1,
                maximum=1,
                group=4,
                requested_group=4,
                stub=3,
            )
            + overlay_stats_line(
                "SAMPLE",
                entries=5,
                exits=2,
                direct=1,
                attempts=4,
                loads=4,
                load_bytes=0x4000,
                depth=2,
                maximum=3,
                group=5,
                requested_group=5,
                stub=9,
            )
            + overlay_stats_line(
                "END",
                entries=6,
                exits=4,
                direct=2,
                attempts=5,
                loads=5,
                load_bytes=0x5000,
                maximum=3,
                group=4,
                requested_group=4,
                stub=12,
            )
            + overlay_stats_line(
                "FINAL",
                entries=6,
                exits=4,
                direct=2,
                attempts=5,
                loads=5,
                load_bytes=0x5000,
                maximum=3,
                group=4,
                requested_group=4,
                stub=12,
            )
            + b"P2PY:OVL:SAMPLE:E=truncated"
        )
        telemetry = hil.parse_overlay_telemetry(incoming)

        self.assertTrue(telemetry["analysis"]["valid"])
        self.assertFalse(telemetry["analysis"]["qualification_valid"])
        self.assertEqual(
            telemetry["analysis"]["stages"],
            ["LAUNCH", "BEGIN", "SAMPLE", "END", "FINAL"],
        )
        self.assertEqual(telemetry["analysis"]["record_count"], 5)
        self.assertEqual(
            telemetry["analysis"]["classification"],
            "overlay-load-progress",
        )
        self.assertEqual(
            telemetry["analysis"]["deltas"]["load_bytes"], 0x5000
        )
        self.assertEqual(telemetry["records"][0]["byte_offset"], 17)
        self.assertEqual(telemetry["records"][2]["current_depth"], 2)
        self.assertFalse(telemetry["records"][2]["transition"])
        self.assertEqual(telemetry["malformed"], [])

    def test_overlay_telemetry_parser_captures_errors_and_malformed_lines(self):
        incoming = (
            b"P2PY:OVL:BEGIN:ERROR=-22\r\n"
            b"P2PY:OVL:UNKNOWN:E=0000000000000000\r\n"
            b"P2PY:OVL:SAMPLE:E=not-hex\r\n"
            b"P2PY:OVL:FINAL:ERROR=-5"
        )
        telemetry = hil.parse_overlay_telemetry(incoming)

        self.assertEqual(telemetry["analysis"]["record_count"], 1)
        self.assertEqual(telemetry["analysis"]["error_count"], 1)
        self.assertEqual(telemetry["records"][0]["kind"], "error")
        self.assertEqual(telemetry["records"][0]["error"], -22)
        self.assertEqual(telemetry["analysis"]["malformed_count"], 2)
        self.assertEqual(len(telemetry["validation_errors"]), 1)
        self.assertFalse(telemetry["analysis"]["valid"])

    def test_overlay_telemetry_invariants_are_fail_closed(self):
        incoming = (
            overlay_stats_line(
                "BEGIN",
                entries=4,
                exits=1,
                direct=1,
                attempts=3,
                loads=1,
                depth=1,
                maximum=0,
            )
            + overlay_stats_line(
                "SAMPLE",
                entries=3,
                exits=1,
                direct=1,
                attempts=2,
                loads=1,
                load_bytes=0x100,
                depth=1,
                maximum=1,
                loading_group=7,
                loading_bytes=0x80,
                flags=1,
            )
        )
        telemetry = hil.parse_overlay_telemetry(incoming)
        errors = "\n".join(telemetry["validation_errors"])

        self.assertIn("entry-direct-exit=depth", errors)
        self.assertIn("maximum depth", errors)
        self.assertIn("attempts-loads 2 while not loading", errors)
        self.assertIn("inconsistent idle loading state", errors)
        self.assertIn("no last request/stub", errors)
        self.assertIn("counter entry_count regressed", errors)
        self.assertIn("counter load_attempt_count regressed", errors)
        self.assertIn("inconsistent load-count/load-byte progress", errors)
        self.assertFalse(telemetry["analysis"]["valid"])

        loading = hil.parse_overlay_telemetry(
            overlay_stats_line(
                "SAMPLE",
                entries=1,
                attempts=2,
                loads=1,
                depth=1,
                maximum=1,
                loading_group=9,
                loading_bytes=0x200,
                requested_group=9,
                stub=2,
                flags=3,
            )
        )
        self.assertTrue(loading["analysis"]["valid"])
        self.assertEqual(
            loading["analysis"]["classification"], "load-in-progress"
        )

    def test_overlay_qualification_accepts_all_launcher_races(self):
        for race in (
            "launch_before_begin",
            "launch_between_begin_end",
            "launch_after_end",
        ):
            with self.subTest(race=race):
                incoming = (
                    qualified_serial(race=race, prompt_prefix=True)
                    + b"partial-console-tail"
                )
                telemetry = hil.parse_overlay_telemetry(
                    incoming, successful_hil_result()
                )
                analysis = telemetry["analysis"]

                self.assertTrue(analysis["record_valid"])
                self.assertTrue(analysis["serial_qualification_valid"])
                self.assertTrue(analysis["qualification_valid"])
                self.assertTrue(analysis["result_checked"])
                self.assertEqual(
                    analysis["lifecycle_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(
                    [item["worker"] for item in telemetry["lifecycles"]],
                    list(hil.EXPECTED_SUCCESSFUL_WORKER_NAMES),
                )
                self.assertEqual(
                    analysis["worker_exit_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(
                    analysis["worker_stack_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(analysis["stage_counts"]["SAMPLE"], 1)
                self.assertTrue(
                    analysis["interactive_repl"]["ordered_within_lifecycle"]
                )
                self.assertEqual(
                    analysis["interactive_repl"]["worker_index"],
                    1,
                )
                self.assertEqual(
                    analysis["race_counts"][race],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(
                    analysis["classification"], "overlay-load-progress"
                )
                self.assertGreater(analysis["deltas"]["entry_count"], 0)
                self.assertGreater(analysis["deltas"]["load_count"], 0)
                self.assertGreater(analysis["deltas"]["load_bytes"], 0)
                initialization = analysis["cpython_initialization"]
                self.assertEqual(initialization["static_type_count"], 113)
                self.assertEqual(initialization["records_per_lifecycle"], 244)
                self.assertEqual(
                    initialization["complete_lifecycle_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(initialization["malformed_count"], 0)
                self.assertIsNone(initialization["first_mismatch"])
                startup = analysis["cpython_startup"]
                self.assertEqual(
                    startup["importlib_pass_marker_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(startup["importlib_malformed_count"], 0)
                self.assertEqual(
                    startup["pathconfig_begin_marker_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(
                    startup["pathconfig_pass_marker_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(startup["pathconfig_fail_marker_count"], 0)
                self.assertEqual(startup["pathconfig_malformed_count"], 0)
                self.assertEqual(
                    startup["complete_pathconfig_lifecycle_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(
                    startup["main_marker_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(startup["main_malformed_count"], 0)
                self.assertEqual(
                    startup["complete_main_lifecycle_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(
                    startup["complete_startup_fill_time_lifecycle_count"],
                    hil.EXPECTED_SUCCESSFUL_WORKERS,
                )
                self.assertEqual(
                    startup["complete_fill_time_call_count"],
                    SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS,
                )
                self.assertEqual(
                    startup["complete_runtime_fill_time_call_count"],
                    SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS,
                )
                self.assertEqual(startup["fill_time_malformed_count"], 0)
                self.assertIsNone(startup["first_fill_time_mismatch"])
                self.assertTrue(
                    all(
                        lifecycle["startup_fill_time_call_count"] == 0
                        for lifecycle in telemetry["lifecycles"]
                    )
                )
                self.assertTrue(
                    all(
                        lifecycle["pathconfig_valid"]
                        for lifecycle in telemetry["lifecycles"]
                    )
                )
                self.assertTrue(
                    all(
                        lifecycle["startup_diagnostics_valid"]
                        for lifecycle in telemetry["lifecycles"]
                    )
                )
                self.assertTrue(analysis["softfloat_probe"]["valid"])
                self.assertEqual(
                    analysis["softfloat_probe"]["fill_time_call_count"],
                    SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS,
                )
                cache = analysis["xmem_cache"]
                self.assertEqual(cache["error_count"], 0)
                self.assertEqual(cache["malformed_count"], 0)
                self.assertEqual(
                    cache["record_count"], analysis["stats_count"]
                )
                self.assertGreater(cache["final"]["hits"], 0)
                self.assertGreater(cache["final"]["misses"], 0)
                self.assertGreater(cache["final"]["fills"], 0)
                self.assertGreater(cache["final"]["writes"], 0)
                self.assertGreater(cache["hit_rate"], 0.9)

        repeated_sample = qualified_serial(
            race="launch_before_begin"
        ).splitlines(keepends=True)
        overlay_sample = next(
            index
            for index, line in enumerate(repeated_sample)
            if line.startswith(b"P2PY:OVL:SAMPLE:")
        )
        repeated_sample.insert(
            overlay_sample + 1, repeated_sample[overlay_sample]
        )
        xmem_sample = next(
            index
            for index, line in enumerate(repeated_sample)
            if line.startswith(b"P2PY:XMEM:SAMPLE:")
        )
        repeated_sample.insert(xmem_sample + 1, repeated_sample[xmem_sample])
        telemetry = hil.parse_overlay_telemetry(
            b"".join(repeated_sample), successful_hil_result()
        )
        self.assertTrue(telemetry["analysis"]["qualification_valid"])
        self.assertEqual(telemetry["analysis"]["stage_counts"]["SAMPLE"], 2)

    def test_smoke_telemetry_qualifies_exactly_one_persistent_lifecycle(self):
        incoming = qualified_serial(
            plan=hil.SMOKE_QUALIFICATION_PLAN,
            sample_indices=(0,),
        )
        result = successful_smoke_result()
        telemetry = hil.parse_overlay_telemetry(
            incoming,
            result,
            hil.SMOKE_QUALIFICATION_PLAN,
        )
        analysis = telemetry["analysis"]
        self.assertTrue(analysis["qualification_valid"])
        self.assertEqual(analysis["expected_lifecycle_count"], 1)
        self.assertEqual(analysis["lifecycle_count"], 1)
        self.assertEqual(
            analysis["expected_worker_names"],
            [hil.INTERACTIVE_REPL_TEST_NAME],
        )
        self.assertEqual(
            analysis["cpython_initialization"]["complete_lifecycle_count"],
            1,
        )
        self.assertEqual(
            analysis["cpython_startup"]["complete_main_lifecycle_count"], 1
        )
        self.assertEqual(
            analysis["cpython_startup"][
                "complete_pathconfig_lifecycle_count"
            ],
            1,
        )
        self.assertEqual(
            analysis["cpython_startup"][
                "complete_startup_fill_time_lifecycle_count"
            ],
            1,
        )
        self.assertEqual(
            [
                lifecycle["startup_fill_time_call_count"]
                for lifecycle in telemetry["lifecycles"]
            ],
            [0],
        )
        self.assertEqual(
            analysis["cpython_startup"]["complete_runtime_fill_time_call_count"],
            SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS,
        )
        self.assertTrue(analysis["softfloat_probe"]["valid"])
        self.assertEqual(analysis["worker_exit_count"], 1)
        self.assertEqual(analysis["worker_stack_count"], 1)
        self.assertEqual(telemetry["lifecycles"][0]["sample_count"], 1)

        sample_in_wrong_worker = qualified_serial(
            plan=hil.SMOKE_QUALIFICATION_PLAN,
            sample_indices=(),
        )
        rejected = hil.parse_overlay_telemetry(
            sample_in_wrong_worker,
            result,
            hil.SMOKE_QUALIFICATION_PLAN,
        )
        self.assertFalse(rejected["analysis"]["qualification_valid"])
        self.assertIn(
            "interactive REPL smoke lifecycle has no live SAMPLE telemetry",
            rejected["qualification_errors"],
        )

        missing_init = incoming.replace(
            b"P2PY:INIT:PYCORE_TYPES:PASS\r\n", b"", 1
        )
        rejected = hil.parse_overlay_telemetry(
            missing_init,
            result,
            hil.SMOKE_QUALIFICATION_PLAN,
        )
        self.assertFalse(rejected["analysis"]["qualification_valid"])

        dishonest = successful_smoke_result()
        dishonest["restart_stress"] = {"skipped": False}
        rejected = hil.parse_overlay_telemetry(
            incoming,
            dishonest,
            hil.SMOKE_QUALIFICATION_PLAN,
        )
        self.assertFalse(rejected["analysis"]["qualification_valid"])
        self.assertIn(
            "restart_stress omission is not explicit",
            rejected["result_validation_errors"],
        )

    def test_cpython_initialization_diagnostics_fail_closed(self):
        base = qualified_serial()
        cases = (
            (
                base.replace(
                    b"P2PY:INIT:GIL:TSTATE:PASS\r\n", b"", 1
                ),
                "CPython initialization marker count",
            ),
            (
                base.replace(
                    b"P2PY:INIT:GC:PASS\r\n", b"", 1
                ),
                "CPython initialization marker count",
            ),
            (
                base.replace(
                    b"P2PY:INIT:GLOBAL_OBJECTS:BEGIN\r\n"
                    b"P2PY:INIT:UNICODE_STATIC:BEGIN\r\n",
                    b"P2PY:INIT:UNICODE_STATIC:BEGIN\r\n"
                    b"P2PY:INIT:GLOBAL_OBJECTS:BEGIN\r\n",
                    1,
                ),
                "CPython initialization sequence first differs",
            ),
            (
                base.replace(
                    b"P2PY:INIT:TYPES:BEGIN:N=113",
                    b"P2PY:INIT:TYPES:BEGIN:N=112",
                    1,
                ),
                "CPython initialization sequence first differs",
            ),
            (
                base.replace(
                    b"P2PY:INIT:TYPE:I=0:AFTER:R=0",
                    b"P2PY:INIT:TYPE:I=0:AFTER:R=-1",
                    1,
                ),
                "CPython initialization sequence first differs",
            ),
            (
                base.replace(
                    b"P2PY:INIT:TYPE:I=0:BEFORE",
                    b"P2PY:INIT:TYPE:I=X:BEFORE",
                    1,
                ),
                "malformed CPython initialization markers",
            ),
        )
        for incoming, expected in cases:
            with self.subTest(expected=expected):
                telemetry = hil.parse_overlay_telemetry(incoming)
                self.assertFalse(telemetry["analysis"]["qualification_valid"])
                self.assertIn(
                    expected, "\n".join(telemetry["qualification_errors"])
                )

    def test_cpython_initialization_records_are_exact_and_ordered(self):
        telemetry = hil.parse_overlay_telemetry(qualified_serial())
        initialization = telemetry["analysis"]["cpython_initialization"]
        records = telemetry["init_records"]

        self.assertTrue(telemetry["analysis"]["qualification_valid"])
        self.assertEqual(initialization["records_per_lifecycle"], 244)
        self.assertEqual(
            len(records),
            244 * hil.EXPECTED_SUCCESSFUL_WORKERS,
        )
        for lifecycle in range(hil.EXPECTED_SUCCESSFUL_WORKERS):
            cycle = records[lifecycle * 244 : (lifecycle + 1) * 244]
            self.assertEqual(
                [record["event"] for record in cycle[:16]],
                [
                    "GIL:TSTATE:PASS",
                    "GIL:READY:PASS",
                    "GLOBAL_OBJECTS:BEGIN",
                    "UNICODE_STATIC:BEGIN",
                    "UNICODE_STATIC:PASS",
                    "LATIN1:BEGIN",
                    "LATIN1:PASS",
                    "GLOBAL_OBJECTS:PASS",
                    "CODE:BEGIN",
                    "CODE:PASS",
                    "DTOA:BEGIN",
                    "DTOA:PASS",
                    "GC:BEGIN",
                    "GC:PASS",
                    "PYCORE_TYPES:BEGIN",
                    "TYPES:BEGIN",
                ],
            )
            self.assertEqual(cycle[15]["count"], 113)
            for index in range(113):
                before = cycle[16 + index * 2]
                after = cycle[17 + index * 2]
                self.assertEqual(
                    (before["event"], before["index"]),
                    ("TYPE:BEFORE", index),
                )
                self.assertEqual(
                    (after["event"], after["index"], after["result"]),
                    ("TYPE:AFTER", index, 0),
                )
            self.assertEqual(
                (cycle[242]["event"], cycle[242]["count"]),
                ("TYPES:PASS", 113),
            )
            self.assertEqual(cycle[243]["event"], "PYCORE_TYPES:PASS")

    def test_fixed_pathconfig_markers_are_exact_ordered_and_bound(self):
        base = qualified_serial()
        telemetry = hil.parse_overlay_telemetry(base)
        startup = telemetry["analysis"]["cpython_startup"]
        importlib_records = telemetry["importlib_pass_records"]
        pathconfig_records = telemetry["pathconfig_records"]
        main_records = telemetry["main_records"]

        self.assertTrue(telemetry["analysis"]["qualification_valid"])
        self.assertEqual(
            len(importlib_records), hil.EXPECTED_SUCCESSFUL_WORKERS
        )
        self.assertEqual(
            [record["event"] for record in pathconfig_records],
            ["BEGIN", "PASS"] * hil.EXPECTED_SUCCESSFUL_WORKERS,
        )
        self.assertEqual(startup["pathconfig_fail_marker_count"], 0)
        self.assertEqual(startup["pathconfig_malformed_count"], 0)
        self.assertEqual(
            startup["complete_pathconfig_lifecycle_count"],
            hil.EXPECTED_SUCCESSFUL_WORKERS,
        )

        for index, lifecycle in enumerate(telemetry["lifecycles"]):
            path_begin = pathconfig_records[index * 2]
            path_pass = pathconfig_records[index * 2 + 1]
            self.assertLess(
                lifecycle["begin_byte_offset"],
                importlib_records[index]["byte_offset"],
            )
            self.assertLess(
                importlib_records[index]["byte_offset"],
                path_begin["byte_offset"],
            )
            self.assertLess(path_begin["byte_offset"], path_pass["byte_offset"])
            self.assertLess(
                path_pass["byte_offset"], main_records[index]["byte_offset"]
            )
            self.assertLess(
                main_records[index]["byte_offset"],
                lifecycle["end_byte_offset"],
            )
            self.assertEqual(lifecycle["importlib_pass_marker_count"], 1)
            self.assertEqual(lifecycle["pathconfig_begin_marker_count"], 1)
            self.assertEqual(lifecycle["pathconfig_pass_marker_count"], 1)
            self.assertEqual(lifecycle["pathconfig_fail_marker_count"], 0)
            self.assertTrue(lifecycle["pathconfig_valid"])

        prompt_prefixed = base.replace(
            hil.PATHCONFIG_BEGIN_MARKER,
            b"nsh> " + hil.PATHCONFIG_BEGIN_MARKER,
            1,
        )
        self.assertTrue(
            hil.parse_overlay_telemetry(prompt_prefixed)["analysis"][
                "qualification_valid"
            ]
        )

    def test_fixed_pathconfig_markers_fail_closed(self):
        base = qualified_serial()
        importlib_line = hil.IMPORTLIB_PASS_MARKER + b"\r\n"
        path_begin_line = hil.PATHCONFIG_BEGIN_MARKER + b"\r\n"
        path_pass_line = hil.PATHCONFIG_PASS_MARKER + b"\r\n"
        path_pair = path_begin_line + path_pass_line
        main_line = hil.MAIN_PASS_MARKER + b"\r\n"

        relocated = base.replace(path_pair, b"", 1)
        relocated = relocated.replace(path_pair, path_pair + path_pair, 1)
        cases = (
            (
                base.replace(importlib_line, b"", 1),
                "CPython IMPORTLIB:PASS marker count",
            ),
            (
                base.replace(importlib_line, b"noise:" + importlib_line, 1),
                "malformed CPython IMPORTLIB:PASS markers",
            ),
            (
                base.replace(path_begin_line, b"", 1),
                "CPython PATHCONFIG:BEGIN marker count",
            ),
            (
                base.replace(path_pass_line, path_pass_line * 2, 1),
                "CPython PATHCONFIG:PASS marker count",
            ),
            (
                base.replace(path_pair, path_pass_line + path_begin_line, 1),
                "lifecycle 1 PATHCONFIG markers",
            ),
            (
                base.replace(
                    importlib_line + path_pair,
                    path_pair + importlib_line,
                    1,
                ),
                "not ordered between IMPORTLIB:PASS and MAIN:PASS",
            ),
            (
                base.replace(path_pair + main_line, main_line + path_pair, 1),
                "not ordered between IMPORTLIB:PASS and MAIN:PASS",
            ),
            (
                base.replace(
                    path_pass_line,
                    hil.PATHCONFIG_FAIL_MARKER + b"\r\n",
                    1,
                ),
                "CPython PATHCONFIG reported FAIL",
            ),
            (
                base.replace(
                    path_begin_line,
                    b"P2PY:PATHCONFIG:UNKNOWN\r\n",
                    1,
                ),
                "malformed CPython PATHCONFIG markers",
            ),
            (
                base.replace(path_begin_line, b"noise:" + path_begin_line, 1),
                "malformed CPython PATHCONFIG markers",
            ),
            (relocated, "lifecycle 1 PATHCONFIG markers []"),
        )
        for incoming, expected in cases:
            with self.subTest(expected=expected):
                telemetry = hil.parse_overlay_telemetry(incoming)
                self.assertFalse(telemetry["analysis"]["qualification_valid"])
                self.assertIn(
                    expected, "\n".join(telemetry["qualification_errors"])
                )

    def test_cpython_main_and_fill_time_diagnostics_are_exact_and_bound(self):
        base = qualified_serial()
        telemetry = hil.parse_overlay_telemetry(base)
        startup = telemetry["analysis"]["cpython_startup"]
        probe = telemetry["analysis"]["softfloat_probe"]

        self.assertTrue(telemetry["analysis"]["qualification_valid"])
        self.assertEqual(
            [record["event"] for record in telemetry["fill_time_records"]],
            list(hil.FILL_TIME_SUCCESS_EVENTS)
            * SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS,
        )
        self.assertEqual(startup["minimum_startup_fill_time_call_count"], 0)
        self.assertEqual(
            startup["complete_runtime_fill_time_call_count"],
            SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS,
        )
        self.assertTrue(
            all(
                lifecycle["startup_fill_time_call_count"] == 0
                for lifecycle in telemetry["lifecycles"]
            )
        )
        self.assertEqual(probe["begin_marker_count"], 1)
        self.assertEqual(probe["pass_marker_count"], 1)
        self.assertEqual(
            probe["fill_time_record_count"],
            SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS
            * hil.FILL_TIME_RECORDS_PER_CALL,
        )
        self.assertEqual(
            probe["fill_time_call_count"],
            SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS,
        )
        self.assertTrue(probe["ordered_within_lifecycle"])
        self.assertTrue(probe["sequence_exact"])
        self.assertTrue(probe["valid"])

        runtime_call = fill_time_diagnostics(call_count=1)
        probe_pass_line = (
            hil.SOFTFLOAT_PROBE_PASS_MARKER.encode("ascii") + b"\r\n"
        )
        with_extra_runtime_call = base.replace(
            probe_pass_line,
            probe_pass_line + runtime_call,
            1,
        )
        runtime = hil.parse_overlay_telemetry(with_extra_runtime_call)
        self.assertTrue(runtime["analysis"]["qualification_valid"])
        self.assertEqual(
            runtime["analysis"]["cpython_startup"][
                "complete_runtime_fill_time_call_count"
            ],
            SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS + 1,
        )
        self.assertEqual(
            runtime["analysis"]["softfloat_probe"]["fill_time_call_count"],
            SOFTFLOAT_PROBE_FILL_TIME_FIXTURE_CALLS,
        )

        main_line = hil.MAIN_PASS_MARKER + b"\r\n"
        with_startup_call = base.replace(
            main_line, runtime_call + main_line, 1
        )
        startup_observed = hil.parse_overlay_telemetry(with_startup_call)
        self.assertTrue(startup_observed["analysis"]["qualification_valid"])
        self.assertEqual(
            startup_observed["lifecycles"][0][
                "startup_fill_time_call_count"
            ],
            1,
        )

        prompt_prefixed = base.replace(
            hil.MAIN_PASS_MARKER,
            b"nsh> " + hil.MAIN_PASS_MARKER,
            1,
        )
        self.assertTrue(
            hil.parse_overlay_telemetry(prompt_prefixed)["analysis"][
                "qualification_valid"
            ]
        )

    def test_cpython_main_and_fill_time_diagnostics_fail_closed(self):
        base = qualified_serial()
        main_line = hil.MAIN_PASS_MARKER + b"\r\n"
        first_call = fill_time_diagnostics(call_count=1)
        all_probe_calls = fill_time_diagnostics()
        probe_begin_line = (
            hil.SOFTFLOAT_PROBE_BEGIN_MARKER.encode("ascii") + b"\r\n"
        )
        probe_pass_line = (
            hil.SOFTFLOAT_PROBE_PASS_MARKER.encode("ascii") + b"\r\n"
        )
        raw_line = (
            b"P2PY:FILLTIME:RAW:SECLO=10203040:SECHI=00000000:"
            b"NSEC=075BCD15\r\n"
        )
        first_final = next(
            line
            for line in base.splitlines(keepends=True)
            if line.startswith(b"P2PY:OVL:FINAL:")
        )

        relocated_main = base.replace(main_line, b"", 1)
        relocated_main = relocated_main.replace(
            main_line, main_line + main_line, 1
        )
        relocated_fill = base.replace(first_call, b"", 1).replace(
            first_final, first_final + first_call, 1
        )
        incomplete_runtime = base.replace(
            probe_pass_line, probe_pass_line + raw_line, 1
        )
        fill_outside_probe = base.replace(all_probe_calls, b"", 1).replace(
            probe_pass_line, probe_pass_line + all_probe_calls, 1
        )

        cases = (
            (
                base.replace(main_line, b"", 1),
                "CPython MAIN:PASS marker count",
            ),
            (
                base.replace(main_line, main_line + main_line, 1),
                "CPython MAIN:PASS marker count",
            ),
            (
                base.replace(main_line, b"noise:" + main_line, 1),
                "malformed CPython MAIN markers",
            ),
            (relocated_main, "lifecycle 1 MAIN:PASS marker count 0 != 1"),
            (
                base.replace(
                    b"P2PY:FILLTIME:FLOATDIDF:PASS\r\n", b"", 1
                ),
                "runtime fill_time record count",
            ),
            (
                base.replace(
                    b"P2PY:FILLTIME:FLOATDIDF:BEGIN\r\n"
                    b"P2PY:FILLTIME:FLOATDIDF:PASS\r\n",
                    b"P2PY:FILLTIME:FLOATDIDF:PASS\r\n"
                    b"P2PY:FILLTIME:FLOATDIDF:BEGIN\r\n",
                    1,
                ),
                "runtime fill_time sequence first differs",
            ),
            (
                base.replace(
                    b"P2PY:FILLTIME:PYFLOAT:PASS\r\n",
                    b"P2PY:FILLTIME:PYFLOAT:FAIL\r\n",
                    1,
                ),
                "runtime fill_time sequence first differs",
            ),
            (
                base.replace(
                    raw_line,
                    raw_line.replace(b"075BCD15", b"75BCD15"),
                    1,
                ),
                "malformed fill_time diagnostic markers",
            ),
            (
                base.replace(raw_line, b"noise:" + raw_line, 1),
                "malformed fill_time diagnostic markers",
            ),
            (
                base.replace(
                    raw_line,
                    raw_line.replace(b"075BCD15", b"3B9ACA00"),
                    1,
                ),
                "fill_time nanoseconds 1000000000 are outside",
            ),
            (
                base.replace(all_probe_calls, b"", 1),
                "soft-float probe fill_time record count 0 !=",
            ),
            (incomplete_runtime, "runtime fill_time record count"),
            (
                base.replace(probe_begin_line, b"", 1),
                "soft-float probe BEGIN marker count 0 != 1",
            ),
            (
                base.replace(
                    probe_pass_line, probe_pass_line + probe_pass_line, 1
                ),
                "soft-float probe PASS marker count 2 != 1",
            ),
            (
                base.replace(
                    probe_pass_line,
                    b"P2PYTEST:SOFTFLOAT:UNKNOWN\r\n",
                    1,
                ),
                "malformed soft-float probe markers",
            ),
            (
                fill_outside_probe,
                "soft-float probe fill_time record count 0 !=",
            ),
            (
                first_call + base,
                "fill_time diagnostic records are outside complete lifecycles",
            ),
            (
                relocated_fill,
                "fill_time diagnostic records are outside complete lifecycles",
            ),
        )
        for incoming, expected in cases:
            with self.subTest(expected=expected):
                telemetry = hil.parse_overlay_telemetry(incoming)
                self.assertFalse(telemetry["analysis"]["qualification_valid"])
                self.assertIn(
                    expected, "\n".join(telemetry["qualification_errors"])
                )

    def test_xmem_cache_telemetry_fails_closed(self):
        base = qualified_serial()
        first = xmem_stats_line(
            "LAUNCH", hits=100, misses=10, fills=9, writes=20, bypasses=5
        )
        missing = base.replace(first, b"", 1)
        malformed = base.replace(
            first,
            b"P2PY:XMEM:LAUNCH:H=BAD:M=000000000000000A:"
            b"F=0000000000000009:W=0000000000000014:"
            b"B=0000000000000005\r\n",
            1,
        )
        api_error = base.replace(
            first, b"P2PY:XMEM:LAUNCH:ERROR=-5\r\n", 1
        )
        fills_exceed_misses = base.replace(
            first,
            xmem_stats_line(
                "LAUNCH",
                hits=100,
                misses=10,
                fills=11,
                writes=20,
                bypasses=5,
            ),
            1,
        )
        final_hits = "H={:016X}".format(
            hil.EXPECTED_SUCCESSFUL_WORKERS * 100
        ).encode("ascii")
        position = base.rfind(final_hits)
        self.assertGreater(position, 0)
        regression = (
            base[:position]
            + b"H=0000000000000001"
            + base[position + len(final_hits) :]
        )
        cases = (
            (missing, "xmem telemetry record count"),
            (malformed, "malformed xmem telemetry records"),
            (api_error, "xmem telemetry API error -5"),
            (fills_exceed_misses, "xmem fills 11 exceed misses 10"),
            (regression, "xmem counter hits regressed"),
        )
        for incoming, expected in cases:
            with self.subTest(expected=expected):
                telemetry = hil.parse_overlay_telemetry(incoming)
                self.assertFalse(telemetry["analysis"]["qualification_valid"])
                errors = "\n".join(
                    telemetry["validation_errors"]
                    + telemetry["qualification_errors"]
                )
                self.assertIn(expected, errors)

    def test_overlay_qualification_rejects_empty_wrong_counts_and_no_sample(self):
        expected = hil.EXPECTED_SUCCESSFUL_WORKERS
        cases = (
            (b"", "LAUNCH stage count 0 != {}".format(expected)),
            (
                qualified_serial(expected - 1),
                "complete lifecycle count {} != {}".format(
                    expected - 1, expected
                ),
            ),
            (
                qualified_serial(expected + 1),
                "complete lifecycle count {} != {}".format(
                    expected + 1, expected
                ),
            ),
            (
                qualified_serial(include_sample=False),
                "SAMPLE stage count must be at least 1",
            ),
            (
                qualified_serial() + b"P2PY:OVL:BEGIN:E=TRUNCATED",
                "trailing incomplete known telemetry marker(s): overlay",
            ),
            (
                qualified_serial() + b"P2PY:WORKER:EXIT:CODE=",
                "trailing incomplete known telemetry marker(s): worker-exit",
            ),
            (
                qualified_serial() + b"P2PY:WORKER:STACK:FREE=1",
                "trailing incomplete known telemetry marker(s): worker-stack",
            ),
            (
                qualified_serial() + b"P2PY:INIT:TYPE:I=12:AFTER:R=",
                "trailing incomplete known telemetry marker(s): cpython-init",
            ),
            (
                qualified_serial() + b"P2PY:IMPORTLIB:",
                "trailing incomplete known telemetry marker(s): cpython-importlib",
            ),
            (
                qualified_serial() + b"P2PY:PATHCONFIG:",
                "trailing incomplete known telemetry marker(s): cpython-pathconfig",
            ),
            (
                qualified_serial() + b"P2PY:MAIN:",
                "trailing incomplete known telemetry marker(s): cpython-main",
            ),
            (
                qualified_serial() + b"P2PY:FILLTIME:FLOATDIDF:",
                "trailing incomplete known telemetry marker(s): fill-time",
            ),
            (
                qualified_serial() + b"P2PY:XMEM:BEGIN:H=",
                "trailing incomplete known telemetry marker(s): xmem",
            ),
        )
        for incoming, expected in cases:
            with self.subTest(expected=expected):
                telemetry = hil.parse_overlay_telemetry(incoming)
                self.assertTrue(telemetry["analysis"]["record_valid"])
                self.assertFalse(telemetry["analysis"]["qualification_valid"])
                self.assertIn(expected, "\n".join(telemetry["qualification_errors"]))

    def test_overlay_qualification_rejects_stage_omissions_duplicates_and_order(self):
        base = qualified_serial()
        lines = base.splitlines(keepends=True)

        first_launch = next(
            index
            for index, line in enumerate(lines)
            if b"P2PY:OVL:LAUNCH:" in line
        )
        first_begin = next(
            index
            for index, line in enumerate(lines)
            if b"P2PY:OVL:BEGIN:" in line
        )
        first_sample = next(
            index
            for index, line in enumerate(lines)
            if b"P2PY:OVL:SAMPLE:" in line
        )
        first_end = next(
            index
            for index, line in enumerate(lines)
            if b"P2PY:OVL:END:" in line
        )

        missing_begin = lines.copy()
        del missing_begin[first_begin]

        duplicate_launch = lines.copy()
        duplicate_launch.insert(first_launch + 1, lines[first_launch])

        end_before_begin = lines.copy()
        end_before_begin[first_begin], end_before_begin[first_end] = (
            end_before_begin[first_end],
            end_before_begin[first_begin],
        )

        sample_before_launch = lines.copy()
        sample = sample_before_launch.pop(first_sample)
        sample_before_launch.insert(first_launch, sample)

        cases = (
            (missing_begin, "lifecycle 1 has 0 BEGIN records"),
            (duplicate_launch, "lifecycle 1 has 2 LAUNCH records"),
            (end_before_begin, "lifecycle 1 END does not follow BEGIN"),
            (sample_before_launch, "lifecycle 1 SAMPLE is outside LAUNCH..FINAL"),
        )
        for mutated, expected in cases:
            with self.subTest(expected=expected):
                telemetry = hil.parse_overlay_telemetry(b"".join(mutated))
                self.assertFalse(telemetry["analysis"]["qualification_valid"])
                self.assertIn(expected, "\n".join(telemetry["qualification_errors"]))

    def test_overlay_qualification_binds_interactive_repl_to_raw_lifecycle(self):
        base = qualified_serial()
        banner = b"Python 3.13.0 test banner\r\n"
        expression = (
            hil.INTERACTIVE_REPL_EXPRESSION_MARKER.encode("ascii") + b"\r\n"
        )
        missing_banner = base.replace(banner, b"", 1)
        missing_expression = base.replace(expression, b"", 1)
        missing_script_begin = base.replace(
            (hil.INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER + "\r\n").encode("ascii"),
            b"",
            1,
        )
        missing_script_pass = base.replace(
            (hil.INTERACTIVE_REPL_SCRIPT_PASS_MARKER + "\r\n").encode("ascii"),
            b"",
            1,
        )
        missing_prompt = base.replace(hil.INTERACTIVE_REPL_PROMPT, b"", 1)
        reordered_expression = expression + base.replace(expression, b"", 1)
        first_setup = hil.persistent_repl_setup_commands(True)[0].encode(
            "ascii"
        )
        corrupted_setup_echo = base.replace(
            first_setup, b"_p2f_broken=" + first_setup, 1
        )

        cases = (
            (missing_banner, "interactive REPL banner count 0 != 1"),
            (
                missing_expression,
                "interactive REPL expression marker count 0 != 1",
            ),
            (
                missing_script_begin,
                "interactive REPL script BEGIN marker count 0 != 1",
            ),
            (
                missing_script_pass,
                "interactive REPL script PASS marker count 0 != 1",
            ),
            (
                missing_prompt,
                "interactive REPL prompt count {} != {}".format(
                    hil.persistent_repl_prompt_count(True) - 1,
                    hil.persistent_repl_prompt_count(True),
                ),
            ),
            (
                reordered_expression,
                "interactive REPL banner/script/prompts/expression are outside",
            ),
            (
                corrupted_setup_echo,
                "interactive REPL setup/execution command echoes are not exact and ordered",
            ),
        )
        for incoming, expected in cases:
            with self.subTest(expected=expected):
                telemetry = hil.parse_overlay_telemetry(incoming)
                self.assertTrue(telemetry["analysis"]["record_valid"])
                self.assertFalse(telemetry["analysis"]["qualification_valid"])
                self.assertIn(expected, "\n".join(telemetry["qualification_errors"]))

    def test_overlay_qualification_requires_ordered_zero_exit_and_stack(self):
        lines = qualified_serial().splitlines(keepends=True)

        first_end = next(
            index
            for index, line in enumerate(lines)
            if b"P2PY:OVL:END:" in line
        )
        first_exit = next(
            index for index, line in enumerate(lines) if line == WORKER_EXIT_LINE
        )
        first_stack = next(
            index for index, line in enumerate(lines) if line == WORKER_STACK_LINE
        )

        missing_exit = lines.copy()
        del missing_exit[first_exit]

        duplicate_stack = lines.copy()
        duplicate_stack.insert(first_stack + 1, WORKER_STACK_LINE)

        nonzero_exit = lines.copy()
        nonzero_exit[first_exit] = b"P2PY:WORKER:EXIT:CODE=3\r\n"

        malformed_stack = lines.copy()
        malformed_stack[first_stack] = b"P2PY:WORKER:STACK:garbage\r\n"

        exit_before_end = lines.copy()
        exit_before_end[first_end], exit_before_end[first_exit] = (
            exit_before_end[first_exit],
            exit_before_end[first_end],
        )

        stack_before_exit = lines.copy()
        stack_before_exit[first_exit], stack_before_exit[first_stack] = (
            stack_before_exit[first_stack],
            stack_before_exit[first_exit],
        )

        cases = (
            (missing_exit, "lifecycle 1 has 0 worker exits"),
            (duplicate_stack, "lifecycle 1 has 2 worker stacks"),
            (nonzero_exit, "worker exited with code 3"),
            (malformed_stack, "malformed worker stack telemetry"),
            (exit_before_end, "worker exit is outside END..FINAL"),
            (stack_before_exit, "worker stack is outside EXIT..FINAL"),
        )
        for mutated, expected in cases:
            with self.subTest(expected=expected):
                telemetry = hil.parse_overlay_telemetry(b"".join(mutated))
                self.assertFalse(telemetry["analysis"]["qualification_valid"])
                self.assertIn(expected, "\n".join(telemetry["qualification_errors"]))

    def test_overlay_records_require_ready_zero_error_and_quiescent_final(self):
        ready_false = qualified_serial().replace(
            b":F=01:ERR=0\r\n", b":F=00:ERR=0\r\n", 1
        )
        nonzero_error = qualified_serial().replace(
            b":F=01:ERR=0\r\n", b":F=01:ERR=-5\r\n", 1
        )

        nonquiescent = qualified_serial().splitlines(keepends=True)
        final_indexes = [
            index
            for index, line in enumerate(nonquiescent)
            if b"P2PY:OVL:FINAL:" in line
        ]
        completed_workers = hil.EXPECTED_SUCCESSFUL_WORKERS
        nonquiescent[final_indexes[-1]] = overlay_stats_line(
            "FINAL",
            entries=completed_workers,
            exits=completed_workers,
            attempts=completed_workers + 1,
            loads=completed_workers,
            load_bytes=completed_workers * 0x1000,
            maximum=1,
            loading_group=5,
            loading_bytes=0x1000,
            requested_group=(completed_workers - 1) % 7 + 1,
            stub=completed_workers,
            flags=3,
        )

        cases = (
            (ready_false, "does not report READY"),
            (nonzero_error, "reports ERR=-5"),
            (b"".join(nonquiescent), "FINAL is not quiescent"),
        )
        for incoming, expected in cases:
            with self.subTest(expected=expected):
                telemetry = hil.parse_overlay_telemetry(incoming)
                self.assertFalse(telemetry["analysis"]["record_valid"])
                self.assertFalse(telemetry["analysis"]["qualification_valid"])
                self.assertIn(expected, "\n".join(telemetry["validation_errors"]))

        retained_group = hil.parse_overlay_telemetry(qualified_serial())
        self.assertTrue(retained_group["analysis"]["qualification_valid"])
        self.assertNotEqual(retained_group["analysis"]["last"]["loaded_group"], 0)

    def test_overlay_transition_and_idle_state_equations_are_fail_closed(self):
        loading = {
            "entries": 1,
            "attempts": 2,
            "loads": 1,
            "depth": 1,
            "maximum": 1,
            "loading_group": 9,
            "loading_bytes": 0x200,
            "requested_group": 9,
            "stub": 2,
            "flags": 3,
        }
        accepted = hil.parse_overlay_telemetry(
            overlay_stats_line("SAMPLE", **loading)
        )
        self.assertTrue(accepted["analysis"]["record_valid"])

        variants = (
            ({**loading, "group": 4}, "inconsistent transition/loading state"),
            ({**loading, "loading_group": 0}, "inconsistent transition/loading state"),
            ({**loading, "loading_bytes": 0}, "inconsistent transition/loading state"),
            ({**loading, "attempts": 1}, "attempts-loads 0 while loading"),
            (
                {
                    **loading,
                    "attempts": 1,
                    "flags": 1,
                },
                "inconsistent idle loading state",
            ),
            ({**loading, "maximum": 65}, "exceeds configured limit 64"),
            ({**loading, "flags": 7}, "unknown flags"),
        )
        for arguments, expected in variants:
            with self.subTest(expected=expected):
                telemetry = hil.parse_overlay_telemetry(
                    overlay_stats_line("SAMPLE", **arguments)
                )
                self.assertFalse(telemetry["analysis"]["record_valid"])
                self.assertIn(expected, "\n".join(telemetry["validation_errors"]))

    def test_overlay_qualification_rejects_zero_progress_and_result_mismatch(self):
        lifecycles = []
        for index in range(hil.EXPECTED_SUCCESSFUL_WORKERS):
            lifecycles.extend(
                (
                    overlay_stats_line("LAUNCH"),
                    overlay_stats_line("BEGIN"),
                )
            )
            if index == 0:
                lifecycles.append(overlay_stats_line("SAMPLE"))
            lifecycles.extend(
                (
                    overlay_stats_line("END"),
                    WORKER_EXIT_LINE,
                    WORKER_STACK_LINE,
                    overlay_stats_line("FINAL"),
                )
            )
        frozen = hil.parse_overlay_telemetry(b"".join(lifecycles))
        frozen_errors = "\n".join(frozen["qualification_errors"])
        self.assertTrue(frozen["analysis"]["record_valid"])
        self.assertFalse(frozen["analysis"]["qualification_valid"])
        self.assertIn("made no overlay entry progress", frozen_errors)
        self.assertIn("entry/load/load-byte deltas must all be positive", frozen_errors)
        self.assertIn("is not overlay-load-progress", frozen_errors)

        mismatched = successful_hil_result()
        mismatched["stack_samples"] = mismatched["stack_samples"][:-1]
        telemetry = hil.parse_overlay_telemetry(qualified_serial(), mismatched)
        self.assertTrue(telemetry["analysis"]["serial_qualification_valid"])
        self.assertFalse(telemetry["analysis"]["qualification_valid"])
        self.assertIn(
            "stack_samples count {} != {}".format(
                hil.EXPECTED_SUCCESSFUL_WORKERS - 1,
                hil.EXPECTED_SUCCESSFUL_WORKERS,
            ),
            "\n".join(telemetry["result_validation_errors"]),
        )

        overnight = hil.OVERNIGHT_QUALIFICATION_PLAN
        detached = successful_hil_result(overnight)
        restart_stacks = [
            dict(sample)
            for sample in detached["restart_stress"]["stack_samples"]
        ]
        restart_stacks[0]["free"] = 1
        detached["restart_stress"]["stack_samples"] = restart_stacks
        concurrency_stacks = [
            dict(sample)
            for sample in detached["concurrency"]["stack_samples"]
        ]
        concurrency_stacks[0]["test"] = "detached-worker"
        detached["concurrency"]["stack_samples"] = concurrency_stacks
        detached["minimum_stack_free"] = -1

        telemetry = hil.parse_overlay_telemetry(
            qualified_serial(plan=overnight), detached, overnight
        )
        result_errors = "\n".join(telemetry["result_validation_errors"])
        self.assertTrue(telemetry["analysis"]["serial_qualification_valid"])
        self.assertFalse(telemetry["analysis"]["qualification_valid"])
        self.assertIn("restart_stress stack samples do not match", result_errors)
        self.assertIn("concurrency stack samples do not match", result_errors)
        self.assertIn("minimum_stack_free -1 does not match", result_errors)

        bad_repl = successful_hil_result()
        bad_repl["interactive_repl"] = dict(bad_repl["interactive_repl"])
        bad_repl["interactive_repl"]["expression_marker"] = "wrong-result"
        telemetry = hil.parse_overlay_telemetry(qualified_serial(), bad_repl)
        self.assertTrue(telemetry["analysis"]["serial_qualification_valid"])
        self.assertFalse(telemetry["analysis"]["qualification_valid"])
        self.assertIn(
            "interactive_repl expression marker is invalid",
            "\n".join(telemetry["result_validation_errors"]),
        )

        bad_setup = successful_hil_result()
        bad_setup["interactive_repl"] = dict(bad_setup["interactive_repl"])
        bad_setup["interactive_repl"]["setup"] = dict(
            bad_setup["interactive_repl"]["setup"]
        )
        bad_setup["interactive_repl"]["setup"]["prompt_ack_count"] -= 1
        telemetry = hil.parse_overlay_telemetry(qualified_serial(), bad_setup)
        self.assertFalse(telemetry["analysis"]["qualification_valid"])
        self.assertIn(
            "interactive_repl setup prompt_ack_count",
            "\n".join(telemetry["result_validation_errors"]),
        )

    def test_atomic_status_writer_replaces_complete_json_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            status_path = root / "status.json"
            hil.write_status_atomic(status_path, {"sequence": 1, "phase": "one"})
            self.assertEqual(
                json.loads(status_path.read_text()),
                {"sequence": 1, "phase": "one"},
            )
            hil.write_status_atomic(
                status_path,
                {"sequence": 2, "phase": "two", "nested": {"ok": True}},
            )
            self.assertEqual(json.loads(status_path.read_text())["sequence"], 2)
            self.assertEqual(list(root.glob(".status.json.*.tmp")), [])

    def test_atomic_status_writer_syncs_file_replace_then_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            status_path = root / "status.json"
            events = []
            real_fsync = hil.os.fsync
            real_replace = hil.os.replace

            def tracked_fsync(descriptor):
                mode = hil.os.fstat(descriptor).st_mode
                events.append(
                    "directory_fsync"
                    if hil.stat.S_ISDIR(mode)
                    else "file_fsync"
                )
                return real_fsync(descriptor)

            def tracked_replace(source, destination):
                events.append("replace")
                return real_replace(source, destination)

            with mock.patch.object(
                hil.os, "fsync", side_effect=tracked_fsync
            ), mock.patch.object(
                hil.os, "replace", side_effect=tracked_replace
            ):
                hil.write_status_atomic(status_path, {"sequence": 1})

            self.assertEqual(
                events,
                ["file_fsync", "replace", "directory_fsync"],
            )

    def test_live_serial_evidence_is_incremental_and_chunk_safe(self):
        sample = overlay_stats_line(
            "SAMPLE",
            entries=3,
            exits=1,
            direct=1,
            attempts=2,
            loads=2,
            load_bytes=0x800,
            depth=1,
            maximum=2,
            group=4,
            requested_group=4,
            stub=7,
        )
        worker_exit = b"nsh> P2PY:WORKER:EXIT:CODE=0\r\n"
        worker_stack = b"P2PY:WORKER:STACK:FREE=4096:SIZE=24576\r\n"
        incomplete = b"P2PY:OVL:SAMP"
        incoming = b"\x00P2AKbinary-prefix" + sample + worker_exit + worker_stack

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            evidence = hil.LiveSerialEvidence(root, sync_interval=3600.0)
            connection = FakeSerial(incoming + incomplete, read_size=3)
            session = hil.SerialSession(connection, evidence)
            session.write(b"partial-host-transmit")
            while connection.incoming:
                session._receive(hil.time.monotonic() + 1.0)

            # A complete telemetry line checkpoints all serial bytes already
            # observed; the final incomplete marker is never called a record.

            snapshot = hil.read_live_serial_evidence(root)
            self.assertEqual(snapshot["serial_rx"], incoming)
            self.assertEqual(
                (root / "serial.raw").read_bytes(), incoming + incomplete
            )
            self.assertEqual(snapshot["serial_tx"], b"partial-host-transmit")
            records = [
                json.loads(line)
                for line in snapshot["telemetry"].splitlines()
            ]
            self.assertEqual(
                [record["kind"] for record in records],
                ["overlay", "worker_exit", "worker_stack"],
            )
            self.assertEqual(
                records[0]["serial_byte_offset"],
                incoming.find(hil.OVERLAY_TELEMETRY_PREFIX),
            )
            self.assertEqual(
                snapshot["progress"]["telemetry_record_count"], 3
            )
            self.assertNotIn("SAMP\"", snapshot["telemetry"].decode())
            self.assertEqual(
                snapshot["progress"]["trailing_serial_line_bytes"], 0
            )
            evidence.close()
            final = hil.read_live_serial_evidence(root)
            self.assertEqual(final["serial_rx"], incoming + incomplete)
            self.assertGreater(
                final["progress"]["trailing_serial_line_bytes"], 0
            )

    def test_live_snapshot_reads_only_the_last_committed_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            evidence = hil.LiveSerialEvidence(root, sync_interval=3600.0)
            evidence.append_rx(b"committed")
            evidence.append_tx(b"sent")
            evidence.checkpoint()
            first = hil.read_live_serial_evidence(root)
            self.assertEqual(first["serial_rx"], b"committed")
            self.assertEqual(first["serial_tx"], b"sent")

            evidence.append_rx(b"-dirty-suffix")
            self.assertEqual(
                (root / "serial.raw").read_bytes(),
                b"committed-dirty-suffix",
            )
            with mock.patch.object(hil, "open_serial") as open_serial:
                second = hil.read_live_serial_evidence(root)
            open_serial.assert_not_called()
            self.assertEqual(second["serial_rx"], b"committed")
            self.assertEqual(
                second["progress"]["serial_rx_committed_bytes"],
                len(b"committed"),
            )

            evidence.close()
            final = hil.read_live_serial_evidence(root)
            self.assertEqual(final["serial_rx"], b"committed-dirty-suffix")
            self.assertEqual(final["progress"]["state"], "closed")

    def test_live_snapshot_rejects_tampered_committed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            evidence = hil.LiveSerialEvidence(root, sync_interval=3600.0)
            evidence.append_rx(b"durable-evidence")
            evidence.close()
            (root / "serial.raw").write_bytes(b"tampered-evidence")
            with self.assertRaisesRegex(
                hil.PythonHilError,
                "committed serial_rx evidence hash does not match",
            ):
                hil.read_live_serial_evidence(root)

    def test_live_checkpoint_commits_data_before_manifest_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            evidence = hil.LiveSerialEvidence(root, sync_interval=3600.0)
            evidence.append_rx(b"new-rx")
            rx_descriptor = evidence._fds["rx"]
            events = []
            real_fsync = hil.os.fsync
            real_replace = hil.os.replace

            def tracked_fsync(descriptor):
                if descriptor == rx_descriptor:
                    events.append("rx_fsync")
                else:
                    mode = hil.os.fstat(descriptor).st_mode
                    events.append(
                        "directory_fsync"
                        if hil.stat.S_ISDIR(mode)
                        else "manifest_fsync"
                    )
                return real_fsync(descriptor)

            def tracked_replace(source, destination):
                events.append("manifest_replace")
                return real_replace(source, destination)

            with mock.patch.object(
                hil.os, "fsync", side_effect=tracked_fsync
            ), mock.patch.object(
                hil.os, "replace", side_effect=tracked_replace
            ):
                evidence.checkpoint()

            self.assertEqual(
                events,
                [
                    "rx_fsync",
                    "manifest_fsync",
                    "manifest_replace",
                    "directory_fsync",
                ],
            )
            evidence.close()

    def test_live_evidence_fsync_failure_is_terminal_and_closes_fds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            evidence = hil.LiveSerialEvidence(root, sync_interval=3600.0)
            descriptors = tuple(evidence._fds.values())
            evidence.append_rx(b"unsynced")
            with mock.patch.object(
                hil.os, "fsync", side_effect=OSError("injected fsync failure")
            ):
                with self.assertRaisesRegex(
                    hil.PythonHilError,
                    "live serial evidence checkpoint failed",
                ):
                    evidence.checkpoint()
            self.assertTrue(evidence.failed)
            evidence.close(checkpoint=False)
            for descriptor in descriptors:
                with self.assertRaises(OSError):
                    hil.os.fstat(descriptor)

    def test_runtime_stages_share_one_overall_deadline_and_report_progress(self):
        class StageSession:
            def __init__(self):
                self.index = 0
                self.timeouts = []

            def wait_line_prefix(self, prefixes, timeout):
                expected = hil.RUNTIME_STAGES[self.index]
                self.assert_expected(expected, prefixes)
                self.timeouts.append(timeout)
                self.index += 1
                return expected

            @staticmethod
            def assert_expected(expected, prefixes):
                if prefixes[0] != expected:
                    raise AssertionError("wrong runtime stage")

        session = StageSession()
        progress = []
        stages, elapsed = hil.wait_runtime_stages(
            session,
            5.0,
            lambda phase, details: progress.append((phase, dict(details))),
        )

        self.assertEqual(stages, [stage.decode("ascii") for stage in hil.RUNTIME_STAGES])
        self.assertGreaterEqual(elapsed, 0.0)
        self.assertEqual(len(session.timeouts), len(hil.RUNTIME_STAGES))
        self.assertTrue(all(0.0 < value <= 5.0 for value in session.timeouts))
        self.assertTrue(
            all(
                later <= earlier
                for earlier, later in zip(session.timeouts, session.timeouts[1:])
            )
        )
        waits = [details for phase, details in progress if details["state"] == "waiting"]
        self.assertEqual(len(waits), len(hil.RUNTIME_STAGES))
        self.assertEqual(waits[0]["completed"], 0)
        self.assertEqual(waits[-1]["next_stage"], hil.RUNTIME_STAGES[-1].decode("ascii"))
        self.assertEqual(progress[-1][1]["state"], "complete")
        self.assertEqual(progress[-1][1]["overall_deadline_seconds"], 5.0)

    def test_runtime_stage_timeout_names_overall_deadline_and_next_marker(self):
        class TimeoutSession:
            def __init__(self):
                self.calls = 0
                self.timeouts = []

            def wait_line_prefix(self, prefixes, timeout):
                self.timeouts.append(timeout)
                self.calls += 1
                if self.calls == 1:
                    return prefixes[0]
                raise hil.PythonHilError("serial receive timeout")

        session = TimeoutSession()
        progress = []
        with mock.patch.object(
            hil.time, "monotonic", side_effect=(100.0, 100.5, 101.0)
        ):
            with self.assertRaisesRegex(
                hil.PythonHilError,
                "runtime-stage overall deadline of 5.000s expired before "
                + re.escape(hil.RUNTIME_STAGES[1].decode("ascii"))
                + " after 1/6 stages",
            ):
                hil.wait_runtime_stages(
                    session,
                    5.0,
                    lambda phase, details: progress.append(
                        (phase, dict(details))
                    ),
                )

        self.assertEqual(session.timeouts, [4.5, 4.0])
        self.assertEqual(progress[-1][1]["completed"], 1)
        self.assertEqual(
            progress[-1][1]["next_stage"],
            hil.RUNTIME_STAGES[1].decode("ascii"),
        )

    def test_missing_python_builtin_fails_before_upload_timeout(self):
        incoming = (
            b"nsh> "
            b"nsh: python: command not found\r\nnsh> "
        )
        with tempfile.TemporaryDirectory() as temporary:
            container = pathlib.Path(temporary) / "python.p2py"
            container.write_bytes(bytes(4096))
            with self.assertRaisesRegex(hil.PythonHilError, "builtin is unavailable"):
                hil.run_python_tests(
                    hil.SerialSession(FakeSerial(incoming)),
                    container,
                    1.0,
                    1.0,
                    1.0,
                )

    def test_run_rejects_upload_pass_with_any_uart_rx_drop(self):
        payload = bytes(4096)
        crc32 = binascii.crc32(payload) & 0xFFFFFFFF
        incoming = (
            b"nsh> "
            b"P2PY:UPLOAD:READY:PROTO=3:BASE=10300000:"
            b"MAX=13631488:FRAME=65536:BAUD=2000000\r\n"
            + "P2PY:UPLOAD:ACCEPT:SIZE=4096:CRC={:08X}\r\n".format(
                crc32
            ).encode("ascii")
            + "P2PY:UPLOAD:PASS:SIZE=4096:CRC={:08X}:RXDROPS=1\r\n".format(
                crc32
            ).encode("ascii")
        )
        with tempfile.TemporaryDirectory() as temporary:
            container = pathlib.Path(temporary) / "python.p2py"
            container.write_bytes(payload)
            with mock.patch.object(
                hil, "send_upload_frames", return_value={}
            ) as send:
                with self.assertRaisesRegex(
                    hil.PythonHilError, "PASS marker does not match"
                ):
                    hil.run_python_tests(
                        hil.SerialSession(FakeSerial(incoming)),
                        container,
                        1.0,
                        1.0,
                        1.0,
                    )
        self.assertFalse(send.call_args.kwargs["inject_faults"])

    def test_smoke_run_sends_only_arithmetic_and_real_repl_workers(self):
        payload = bytes(4096)
        crc32 = binascii.crc32(payload) & 0xFFFFFFFF
        setup_commands = hil.persistent_repl_setup_commands(False)
        setup_exchange = b"".join(
            command.encode("ascii") + b"\r\n>>> "
            for command in setup_commands
        )
        incoming = (
            b"nsh> "
            b"P2PY:UPLOAD:READY:PROTO=3:BASE=10300000:"
            b"MAX=13631488:FRAME=65536:BAUD=2000000\r\n"
            + "P2PY:UPLOAD:ACCEPT:SIZE=4096:CRC={:08X}\r\n".format(
                crc32
            ).encode("ascii")
            + "P2PY:UPLOAD:PASS:SIZE=4096:CRC={:08X}:RXDROPS=0\r\n".format(
                crc32
            ).encode("ascii")
            + b"P2PY:RUNTIME:READY:ROMFS=1:GROUPS=1:SLOT=00066000+90112\r\n"
            + b"".join(stage + b"\r\n" for stage in hil.RUNTIME_STAGES)
            + b"Python 3.13.0 test banner\r\n>>> "
            + setup_exchange
            + hil.persistent_repl_exec_command().encode("ascii")
            + b"\r\n"
            + hil.INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER.encode("ascii")
            + b"\r\n"
            + b"P2PYTEST:ARITH:PASS\r\n"
            + hil.INTERACTIVE_REPL_SCRIPT_PASS_MARKER.encode("ascii")
            + b"\r\n>>> "
            + hil.INTERACTIVE_REPL_EXPRESSION_COMMAND.encode("ascii")
            + b"\r\n"
            + hil.INTERACTIVE_REPL_EXPRESSION_MARKER.encode("ascii")
            + b"\r\n>>> "
            + hil.INTERACTIVE_REPL_EXIT_COMMAND.encode("ascii")
            + b"\r\n"
            + WORKER_EXIT_LINE
            + WORKER_STACK_LINE
            + b"nsh> "
        )
        live_hold = {
            "requested_seconds": hil.SMOKE_REPL_LIVE_HOLD_SECONDS,
            "elapsed_seconds": hil.SMOKE_REPL_LIVE_HOLD_SECONDS,
            "sample_marker": overlay_stats_line("SAMPLE").decode(
                "ascii"
            ).rstrip("\r\n"),
        }

        with tempfile.TemporaryDirectory() as temporary:
            container = pathlib.Path(temporary) / "python.p2py"
            container.write_bytes(payload)
            fake = FakeSerial(incoming)
            with mock.patch.object(
                hil, "send_upload_frames", return_value={}
            ):
                with mock.patch.object(
                    hil,
                    "hold_interactive_repl_alive",
                    return_value=live_hold,
                ) as hold:
                    result = hil.run_python_tests(
                        hil.SerialSession(fake),
                        container,
                        10.0,
                        10.0,
                        hil.SMOKE_REPL_LIVE_HOLD_SECONDS + 1.0,
                        plan=hil.SMOKE_QUALIFICATION_PLAN,
                    )

        self.assertEqual(
            result["completed_tests"],
            ["arithmetic", hil.INTERACTIVE_REPL_TEST_NAME],
        )
        self.assertTrue(result["restart_stress"]["skipped"])
        self.assertTrue(result["concurrency"]["skipped"])
        hold.assert_called_once_with(
            mock.ANY, hil.SMOKE_REPL_LIVE_HOLD_SECONDS
        )
        sent = bytes(fake.outgoing)
        self.assertIn(hil.QUALIFICATION_BATCH_PATH.encode("ascii"), sent)
        self.assertIn(b"python\r", sent)
        self.assertIn(hil.INTERACTIVE_REPL_EXPRESSION_COMMAND.encode("ascii"), sent)
        self.assertIn(hil.INTERACTIVE_REPL_EXIT_COMMAND.encode("ascii"), sent)
        for test in hil.PYTHON_TESTS[1:]:
            self.assertNotIn(test.command.encode("ascii"), sent)
        self.assertNotIn(b"P2PYTEST:RESTART:", sent)
        self.assertNotIn(hil.CONCURRENCY_HOLDER_COMMAND.encode("ascii"), sent)

    def test_full_regular_assertions_run_in_exactly_three_workers(self):
        payload = bytes(4096)
        crc32 = binascii.crc32(payload) & 0xFFFFFFFF
        plan = hil.PythonQualificationPlan(
            level="regular-batch-test",
            python_tests=hil.PYTHON_TESTS,
            python_workers=hil.FULL_PYTHON_TEST_WORKERS,
            include_restart_stress=False,
            include_concurrency=True,
            full_qualification=True,
        )
        incoming = [
            b"nsh> ",
            b"P2PY:UPLOAD:READY:PROTO=3:BASE=10300000:"
            b"MAX=13631488:FRAME=65536:BAUD=2000000\r\n",
            "P2PY:UPLOAD:ACCEPT:SIZE=4096:CRC={:08X}\r\n".format(
                crc32
            ).encode("ascii"),
            "P2PY:UPLOAD:PASS:SIZE=4096:CRC={:08X}:RXDROPS=0\r\n".format(
                crc32
            ).encode("ascii"),
            b"P2PY:RUNTIME:READY:ROMFS=1:GROUPS=1:SLOT=00066000+90112\r\n",
            b"".join(stage + b"\r\n" for stage in hil.RUNTIME_STAGES),
        ]
        repl_worker, holder_worker, post_worker = plan.python_workers
        setup_commands = hil.persistent_repl_setup_commands(True)
        incoming.extend(
            (
                b"Python 3.13.0 test banner\r\n>>> ",
                b"".join(
                    command.encode("ascii") + b"\r\n>>> "
                    for command in setup_commands
                ),
                hil.persistent_repl_exec_command().encode("ascii") + b"\r\n",
                hil.INTERACTIVE_REPL_SCRIPT_BEGIN_MARKER.encode("ascii") + b"\r\n",
            )
        )
        for test in repl_worker.tests:
            if test.name == "hardware_entropy":
                incoming.append(
                    b"P2PYTEST:ENTROPY:FINGERPRINT:"
                    b"0123456789abcdef0123456789abcdef\r\n"
                )
            incoming.append(test.marker.encode("ascii") + b"\r\n")
        incoming.extend(
            (
                hil.INTERACTIVE_REPL_SCRIPT_PASS_MARKER.encode("ascii") + b"\r\n>>> ",
                hil.INTERACTIVE_REPL_EXPRESSION_MARKER.encode("ascii") + b"\r\n>>> ",
                WORKER_EXIT_LINE,
                WORKER_STACK_LINE,
                b"nsh> ",
                b"nsh> ",
            )
        )
        incoming.extend(test.marker.encode("ascii") + b"\r\n" for test in holder_worker.tests)
        incoming.extend(
            (
                hil.CONCURRENCY_HOLDER_MARKER.encode("ascii") + b"\r\n",
                (hil.CONCURRENCY_BUSY_PREFIX + "16\r\nnsh> ").encode("ascii"),
                hil.CONCURRENCY_DONE_MARKER.encode("ascii") + b"\r\n",
                WORKER_EXIT_LINE,
                WORKER_STACK_LINE,
            )
        )
        incoming.extend(test.marker.encode("ascii") + b"\r\n" for test in post_worker.tests)
        incoming.extend(
            (
                hil.CONCURRENCY_POST_MARKER.encode("ascii") + b"\r\n",
                WORKER_EXIT_LINE,
                WORKER_STACK_LINE,
                b"nsh> ",
            )
        )

        with tempfile.TemporaryDirectory() as temporary:
            container = pathlib.Path(temporary) / "python.p2py"
            container.write_bytes(payload)
            fake = FakeSerial(b"".join(incoming), read_size=1024)
            with mock.patch.object(hil, "send_upload_frames", return_value={}):
                result = hil.run_python_tests(
                    hil.SerialSession(fake),
                    container,
                    10.0,
                    10.0,
                    10.0,
                    plan=plan,
                )

        self.assertEqual(result["completed_tests"], list(plan.completed_test_names))
        self.assertEqual(
            [sample["test"] for sample in result["stack_samples"]],
            [worker.name for worker in plan.python_workers],
        )
        self.assertEqual(len(result["worker_durations_seconds"]), 3)
        self.assertEqual(len(result["test_durations_seconds"]), 26)
        self.assertEqual(
            len(result["shell_setup"]),
            sum(len(worker.setup_commands) for worker in plan.python_workers),
        )
        sent = bytes(fake.outgoing)
        self.assertTrue(sent.startswith(b"\rpython\r"))
        self.assertIn(hil.CONCURRENCY_HOLDER_COMMAND.encode("ascii"), sent)
        self.assertIn(hil.CONCURRENCY_POST_COMMAND.encode("ascii"), sent)
        for test in hil.QUALIFICATION_BATCH_TESTS:
            self.assertNotIn((test.command + "\r").encode("ascii"), sent)

    def test_serial_session_handles_partial_reads_and_writes(self):
        fake = FakeSerial(b"noise\r\nP2PY:READY\r\nremaining")
        session = hil.SerialSession(fake)
        session.write(b"0123456789")
        self.assertEqual(bytes(fake.outgoing), b"0123456789")
        self.assertEqual(fake.flushes, 0)
        line = session.wait_line_prefix((b"P2PY:",), 1.0)
        self.assertEqual(line, b"P2PY:READY")
        self.assertEqual(session.read_exact(9, 1.0), b"remaining")

    def test_serial_session_aborts_on_xmem_fault_without_waiting_for_newline(self):
        fake = FakeSerial(b"noise\r\nP2XMEM:FAULT", read_size=2)
        session = hil.SerialSession(fake)

        with self.assertRaisesRegex(
            hil.PythonHilError,
            "target reported fatal serial marker P2XMEM:FAULT",
        ):
            session.wait_line_prefix((b"P2PY:NEVER",), 10.0)

        self.assertTrue(bytes(session.received).endswith(b"P2XMEM:FAULT"))

    def test_serial_session_aborts_on_xmem_timeout_without_newline(self):
        fake = FakeSerial(b"noise\r\nP2XMEM:TIMEOUT", read_size=2)
        session = hil.SerialSession(fake)

        with self.assertRaisesRegex(
            hil.PythonHilError,
            "target reported fatal serial marker P2XMEM:TIMEOUT",
        ):
            session.wait_line_prefix((b"P2PY:NEVER",), 10.0)

        self.assertTrue(bytes(session.received).endswith(b"P2XMEM:TIMEOUT"))

    def test_serial_session_enforces_write_deadline_without_flush(self):
        fake = FakeSerial(b"")
        session = hil.SerialSession(fake)
        with mock.patch.object(
            hil.time, "monotonic", side_effect=(0.0, 0.1, 0.2, 0.3)
        ):
            session.write(b"0123456789", deadline=1.0)
        self.assertEqual(bytes(fake.outgoing), b"0123456789")
        self.assertEqual(fake.flushes, 0)
        self.assertEqual(fake.write_timeout, 10.0)
        self.assertAlmostEqual(fake.write_timeouts[0], 1.0)
        self.assertAlmostEqual(fake.write_timeouts[1], 0.8)

        expired = FakeSerial(b"")
        expired_session = hil.SerialSession(expired)
        with mock.patch.object(
            hil.time, "monotonic", side_effect=(0.0, 1.1)
        ):
            with self.assertRaisesRegex(
                hil.PythonHilError, "exceeded its deadline"
            ):
                expired_session.write(b"x", deadline=1.0)
        self.assertEqual(bytes(expired_session.sent), b"x")
        self.assertEqual(expired.flushes, 0)
        self.assertEqual(expired.write_timeout, 10.0)

    def test_serial_session_keeps_raw_frame_descriptor_nonblocking(self):
        read_fd, write_fd = os.pipe()

        class PipeConnection:
            def fileno(self):
                return write_fd

        try:
            flags = fcntl.fcntl(write_fd, fcntl.F_GETFL)
            fcntl.fcntl(write_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            session = hil.SerialSession(PipeConnection())
            self.assertNotEqual(
                fcntl.fcntl(write_fd, fcntl.F_GETFL) & os.O_NONBLOCK, 0
            )
            session.write_blocking(b"blocking-frame")
            self.assertEqual(os.read(read_fd, 14), b"blocking-frame")
            self.assertEqual(bytes(session.sent), b"blocking-frame")
        finally:
            os.close(write_fd)
            os.close(read_fd)

    def test_raw_descriptor_write_deadline_bounds_a_full_tty_queue(self):
        read_fd, write_fd = os.pipe()

        class PipeConnection:
            def fileno(self):
                return write_fd

        try:
            session = hil.SerialSession(PipeConnection())
            while True:
                try:
                    os.write(write_fd, bytes(4096))
                except BlockingIOError:
                    break

            deadline = hil.time.monotonic() + 0.01
            with self.assertRaisesRegex(
                hil.PythonHilError, "exceeded its deadline"
            ):
                session.write_blocking(b"x", deadline=deadline)
            self.assertEqual(bytes(session.sent), b"")
        finally:
            os.close(write_fd)
            os.close(read_fd)

    def test_loadp2_releases_serial_before_binary_upload(self):
        args = types.SimpleNamespace(
            loadp2=pathlib.Path("/pinned/loadp2"),
            serial="/dev/cu.board",
            loader_baud=2000000,
            baud=hil.RUNTIME_BAUD,
            reset_method="dtr",
            image=pathlib.Path("/artifacts/nuttx.bin"),
        )
        command = hil.loader_command(args)
        self.assertNotIn("-t", command)
        self.assertEqual(command[-1], "/artifacts/nuttx.bin")
        self.assertEqual(command[command.index("-p") + 1], "/dev/cu.board")
        completed = subprocess.CompletedProcess(command, 0, stdout=b"loaded")
        with mock.patch.object(hil.subprocess, "run", return_value=completed) as run:
            self.assertIs(hil.run_loader(command, 1.0), completed)
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("input", run.call_args.kwargs)

    def test_logical_frames_stream_through_bounded_blocking_serial_writes(self):
        _committed, frame = next(
            iter(
                hil.upload_frames(
                    io.BytesIO(bytes(hil.UPLOAD_FRAME_SIZE)),
                    hil.UPLOAD_FRAME_SIZE,
                )
            )
        )
        self.assertEqual(
            len(frame), hil.UPLOAD_FRAME.size + hil.UPLOAD_FRAME_SIZE
        )
        self.assertGreater(len(frame), hil.MAX_UART_WRITE)

        class CaptureSession:
            def __init__(self):
                self.writes = []

            def write(self, data, deadline=None):
                raise AssertionError("upload bypassed blocking frame write")

            def write_blocking(self, data, deadline=None):
                self.writes.append(bytes(data))

        capture = CaptureSession()
        with mock.patch.object(hil.time, "sleep") as sleep:
            hil.send_logical_frame(capture, frame)
        self.assertEqual(b"".join(capture.writes), frame)
        self.assertTrue(capture.writes)
        self.assertTrue(
            all(0 < len(write) <= hil.MAX_UART_WRITE for write in capture.writes)
        )
        sleep.assert_not_called()
        expected_writes = (
            len(frame) + hil.UPLOAD_WIRE_CHUNK_SIZE - 1
        ) // hil.UPLOAD_WIRE_CHUNK_SIZE
        self.assertEqual(len(capture.writes), expected_writes)
        self.assertEqual(hil.UPLOAD_WIRE_CHUNK_SIZE, 1024)
        self.assertLessEqual(hil.UPLOAD_WIRE_CHUNK_SIZE, hil.MAX_UART_WRITE)
        self.assertEqual(hil.UPLOAD_CHUNK_GAP_SECONDS, 0.0)
        self.assertAlmostEqual(
            hil.UPLOAD_CHUNK_WIRE_SECONDS, 10240 / 2000000
        )
        self.assertEqual(hil.UPLOAD_CHUNK_PAUSE_SECONDS, 0.0)
        self.assertEqual(hil.UPLOAD_FRAME_SIZE, 65536)
        self.assertEqual(len(frame), 65548)

        fake = FakeSerial(b"")
        session = hil.SerialSession(fake)
        with self.assertRaises(hil.PythonHilError):
            session.write(bytes(hil.MAX_UART_WRITE + 1))

        self.assertEqual(hil.UPLOAD_WINDOW_FRAMES, 1)

    def test_logical_frame_checks_deadline_before_each_chunk(self):
        frame = bytes(hil.UPLOAD_WIRE_CHUNK_SIZE + 1)

        class CaptureSession:
            def __init__(self):
                self.writes = []

            def write(self, data, deadline=None):
                self.writes.append(bytes(data))

        capture = CaptureSession()
        with mock.patch.object(
            hil.time, "monotonic", side_effect=(0.0, 0.011)
        ):
            with mock.patch.object(hil.time, "sleep") as sleep:
                with self.assertRaisesRegex(
                    hil.PythonHilError, "exceeded its deadline"
                ):
                    hil.send_logical_frame(capture, frame, deadline=0.010)
        self.assertEqual(len(capture.writes), 1)
        sleep.assert_not_called()

    def test_upload_is_strictly_stop_and_wait(self):
        payload = bytes(
            (index * 17) & 0xFF
            for index in range(4 * hil.UPLOAD_FRAME_SIZE + 5000)
        )

        class StopAndWaitSession:
            def __init__(self):
                self.current = bytearray()
                self.transmissions = []
                self.events = []

            def write(self, chunk, deadline=None):
                if len(chunk) > hil.MAX_UART_WRITE:
                    raise AssertionError("unbounded UART write")
                self.current.extend(chunk)
                self.events.append(("write", len(chunk)))

            def read_exact(self, response_size, _timeout):
                if response_size != hil.UPLOAD_ACK.size:
                    raise AssertionError("wrong response size")
                frame = bytes(self.current)
                self.current.clear()
                offset, size, _crc = hil.UPLOAD_FRAME.unpack(
                    frame[: hil.UPLOAD_FRAME.size]
                )
                if len(frame) != hil.UPLOAD_FRAME.size + size:
                    raise AssertionError("more than one logical frame in flight")
                self.transmissions.append(frame)
                self.events.append(("response", offset + size))
                return hil.UPLOAD_ACK.pack(
                    hil.UPLOAD_ACK_MAGIC, offset + size
                )

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            session = StopAndWaitSession()
            progress = []
            with mock.patch.object(hil.time, "sleep"), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = hil.send_upload_frames(
                    session,
                    path,
                    len(payload),
                    10.0,
                    1.0,
                    progress_callback=lambda phase, details: progress.append(
                        (phase, dict(details))
                    ),
                )

        self.assertFalse(session.current)
        self.assertEqual(len(session.transmissions), 5)
        self.assertEqual(result["frame_count"], 5)
        self.assertEqual(result["frame_transmissions"], 5)
        self.assertEqual(result["frame_retries"], 0)
        self.assertFalse(result["fault_injection_enabled"])
        self.assertEqual(result["injected_fault_count"], 0)
        self.assertEqual(result["injected_fault_kinds"], [])
        self.assertEqual(result["window_frames"], 1)
        self.assertEqual(result["window_count"], 5)
        self.assertEqual(result["wire_chunk_bytes"], 1024)
        self.assertAlmostEqual(
            result["wire_chunk_seconds"], 10240 / 2000000
        )
        self.assertEqual(result["inter_chunk_gap_seconds"], 0.0)
        self.assertEqual(result["inter_chunk_pause_seconds"], 0.0)
        self.assertEqual(
            [event for event, _value in session.events].count("response"),
            5,
        )
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0][0], "upload_frames")
        self.assertEqual(progress[0][1]["acked_bytes"], len(payload))
        self.assertEqual(progress[0][1]["frame_count"], 5)

    def test_explicit_nack_retransmits_the_exact_logical_frame(self):
        payload = bytes(
            (index * 31) & 0xFF
            for index in range(hil.UPLOAD_FRAME_SIZE + 1500)
        )

        class RetrySession:
            def __init__(self):
                self.current = bytearray()
                self.transmissions = []

            def write(self, chunk, deadline=None):
                self.current.extend(chunk)

            def read_exact(self, _size, _timeout):
                frame = bytes(self.current)
                self.current.clear()
                offset, size, _crc = hil.UPLOAD_FRAME.unpack(
                    frame[: hil.UPLOAD_FRAME.size]
                )
                self.transmissions.append(frame)
                if len(self.transmissions) == 1:
                    return hil.UPLOAD_ACK.pack(hil.UPLOAD_NACK_MAGIC, offset)
                return hil.UPLOAD_ACK.pack(hil.UPLOAD_ACK_MAGIC, offset + size)

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            session = RetrySession()
            with mock.patch.object(hil.time, "sleep"), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = hil.send_upload_frames(
                    session, path, len(payload), 10.0, 1.0
                )

        self.assertEqual(session.transmissions[0], session.transmissions[1])
        self.assertEqual(result["frame_count"], 2)
        self.assertEqual(result["frame_transmissions"], 3)
        self.assertEqual(result["frame_retries"], 1)

    def test_fault_qualification_nacks_then_exactly_retransmits_all_faults(self):
        payload = bytes(
            (index * 43 + 9) & 0xFF
            for index in range(2 * hil.UPLOAD_FRAME_SIZE + 173)
        )

        class CheckingTargetSession:
            def __init__(self, total_size):
                self.total_size = total_size
                self.committed = 0
                self.current = bytearray()
                self.chunks = []
                self.transmissions = []
                self.responses = []

            def write(self, chunk, deadline=None):
                self.assert_chunk_bound(chunk)
                self.current.extend(chunk)
                self.chunks.append(bytes(chunk))

            @staticmethod
            def assert_chunk_bound(chunk):
                if not 0 < len(chunk) <= hil.MAX_UART_WRITE:
                    raise AssertionError("unbounded UART write")

            def read_exact(self, response_size, _timeout):
                if response_size != hil.UPLOAD_ACK.size:
                    raise AssertionError("wrong response size")
                frame = bytes(self.current)
                self.current.clear()
                expected_size = min(
                    hil.UPLOAD_FRAME_SIZE, self.total_size - self.committed
                )
                if len(frame) != hil.UPLOAD_FRAME.size + expected_size:
                    raise AssertionError("target did not receive exact wire length")
                frame_offset, declared_size, declared_crc = hil.UPLOAD_FRAME.unpack(
                    frame[: hil.UPLOAD_FRAME.size]
                )
                frame_payload = frame[hil.UPLOAD_FRAME.size :]
                valid = (
                    frame_offset == self.committed
                    and declared_size == expected_size
                    and declared_crc
                    == (binascii.crc32(frame_payload) & 0xFFFFFFFF)
                )
                self.transmissions.append(frame)
                if valid:
                    self.committed += expected_size
                    response = hil.UPLOAD_ACK.pack(
                        hil.UPLOAD_ACK_MAGIC, self.committed
                    )
                else:
                    response = hil.UPLOAD_ACK.pack(
                        hil.UPLOAD_NACK_MAGIC, self.committed
                    )
                self.responses.append(hil.UPLOAD_ACK.unpack(response))
                return response

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            target = CheckingTargetSession(len(payload))
            with mock.patch.object(hil.time, "sleep"), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = hil.send_upload_frames(
                    target,
                    path,
                    len(payload),
                    10.0,
                    1.0,
                    inject_faults=True,
                )

        correct = [
            frame for _committed, frame in hil.upload_frames(
                io.BytesIO(payload), len(payload)
            )
        ]
        self.assertEqual(target.committed, len(payload))
        self.assertEqual(len(target.transmissions), 6)
        self.assertEqual(target.transmissions[1], correct[0])
        self.assertEqual(target.transmissions[3], correct[1])
        self.assertEqual(target.transmissions[5], correct[2])
        self.assertEqual(
            target.responses,
            [
                (hil.UPLOAD_NACK_MAGIC, 0),
                (hil.UPLOAD_ACK_MAGIC, hil.UPLOAD_FRAME_SIZE),
                (hil.UPLOAD_NACK_MAGIC, hil.UPLOAD_FRAME_SIZE),
                (hil.UPLOAD_ACK_MAGIC, 2 * hil.UPLOAD_FRAME_SIZE),
                (hil.UPLOAD_NACK_MAGIC, 2 * hil.UPLOAD_FRAME_SIZE),
                (hil.UPLOAD_ACK_MAGIC, len(payload)),
            ],
        )

        bad_crc = hil.UPLOAD_FRAME.unpack(
            target.transmissions[0][: hil.UPLOAD_FRAME.size]
        )
        good_first = hil.UPLOAD_FRAME.unpack(correct[0][: hil.UPLOAD_FRAME.size])
        self.assertEqual(bad_crc[:2], good_first[:2])
        self.assertEqual(bad_crc[2], good_first[2] ^ 1)
        self.assertEqual(
            target.transmissions[0][hil.UPLOAD_FRAME.size :],
            correct[0][hil.UPLOAD_FRAME.size :],
        )

        bad_offset = hil.UPLOAD_FRAME.unpack(
            target.transmissions[2][: hil.UPLOAD_FRAME.size]
        )
        good_second = hil.UPLOAD_FRAME.unpack(correct[1][: hil.UPLOAD_FRAME.size])
        self.assertEqual(bad_offset[0], good_second[0] + 1)
        self.assertEqual(bad_offset[1:], good_second[1:])
        self.assertEqual(
            target.transmissions[2][hil.UPLOAD_FRAME.size :],
            correct[1][hil.UPLOAD_FRAME.size :],
        )

        bad_size = hil.UPLOAD_FRAME.unpack(
            target.transmissions[4][: hil.UPLOAD_FRAME.size]
        )
        good_final = hil.UPLOAD_FRAME.unpack(correct[2][: hil.UPLOAD_FRAME.size])
        self.assertEqual(bad_size[0], good_final[0])
        self.assertEqual(bad_size[1], good_final[1] + 1)
        self.assertEqual(bad_size[2], good_final[2])
        self.assertEqual(len(target.transmissions[4]), len(correct[2]))
        self.assertEqual(
            target.transmissions[4][hil.UPLOAD_FRAME.size :],
            correct[2][hil.UPLOAD_FRAME.size :],
        )

        self.assertEqual(result["frame_count"], 3)
        self.assertEqual(result["frame_transmissions"], 6)
        self.assertEqual(result["frame_retries"], 3)
        self.assertTrue(result["fault_injection_enabled"])
        self.assertEqual(result["injected_fault_count"], 3)
        self.assertEqual(
            result["injected_fault_kinds"], list(hil.UPLOAD_FAULT_SEQUENCE)
        )
        self.assertEqual(
            [fault["frame_offset"] for fault in result["injected_faults"]],
            [0, hil.UPLOAD_FRAME_SIZE, 2 * hil.UPLOAD_FRAME_SIZE],
        )

    def test_deliberate_nack_consumes_the_shared_attempt_budget(self):
        payload = bytes(2 * hil.UPLOAD_FRAME_SIZE + 1)

        class RejectingTarget:
            def __init__(self):
                self.current = bytearray()
                self.transmissions = []

            def write(self, chunk, deadline=None):
                self.current.extend(chunk)

            def read_exact(self, _size, _timeout):
                self.transmissions.append(bytes(self.current))
                self.current.clear()
                return hil.UPLOAD_ACK.pack(hil.UPLOAD_NACK_MAGIC, 0)

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            target = RejectingTarget()
            with mock.patch.object(hil.time, "sleep"):
                with self.assertRaisesRegex(
                    hil.PythonHilError, "after 3 retries"
                ):
                    hil.send_upload_frames(
                        target,
                        path,
                        len(payload),
                        10.0,
                        1.0,
                        inject_faults=True,
                    )

        correct_first = next(
            iter(hil.upload_frames(io.BytesIO(payload), len(payload)))
        )[1]
        self.assertEqual(len(target.transmissions), 4)
        self.assertNotEqual(target.transmissions[0], correct_first)
        self.assertEqual(target.transmissions[1:], [correct_first] * 3)

    def test_fault_injection_fails_closed_on_any_nonexact_nack(self):
        payload = bytes(2 * hil.UPLOAD_FRAME_SIZE + 1)
        cases = (
            (
                hil.UPLOAD_ACK.pack(
                    hil.UPLOAD_ACK_MAGIC, hil.UPLOAD_FRAME_SIZE
                ),
                "ACKed deliberately invalid",
            ),
            (
                hil.UPLOAD_ACK.pack(hil.UPLOAD_NACK_MAGIC, 1),
                "invalid upload NACK",
            ),
            (hil.UPLOAD_ACK.pack(b"NOPE", 0), "invalid upload response"),
        )

        for response, message in cases:
            with self.subTest(response=response):
                class ResponseTarget:
                    def __init__(self):
                        self.current = bytearray()
                        self.transmissions = []

                    def write(self, chunk, deadline=None):
                        self.current.extend(chunk)

                    def read_exact(self, _size, _timeout):
                        self.transmissions.append(bytes(self.current))
                        self.current.clear()
                        return response

                with tempfile.TemporaryDirectory() as temporary:
                    path = pathlib.Path(temporary) / "payload"
                    path.write_bytes(payload)
                    target = ResponseTarget()
                    with mock.patch.object(hil.time, "sleep"):
                        with self.assertRaisesRegex(hil.PythonHilError, message):
                            hil.send_upload_frames(
                                target,
                                path,
                                len(payload),
                                10.0,
                                1.0,
                                inject_faults=True,
                            )
                self.assertEqual(len(target.transmissions), 1)

    def test_fault_qualification_requires_a_distinct_short_final_frame(self):
        for size in (2 * hil.UPLOAD_FRAME_SIZE, 3 * hil.UPLOAD_FRAME_SIZE):
            with self.subTest(size=size):
                with tempfile.TemporaryDirectory() as temporary:
                    path = pathlib.Path(temporary) / "payload"
                    path.write_bytes(bytes(size))
                    with self.assertRaisesRegex(
                        hil.PythonHilError, "distinct short final frame"
                    ):
                        hil.send_upload_frames(
                            mock.Mock(),
                            path,
                            size,
                            10.0,
                            1.0,
                            inject_faults=True,
                        )

    def test_upload_rejects_mismatched_or_unknown_responses(self):
        payload = b"x" * 192
        cases = (
            (hil.UPLOAD_ACK_MAGIC, 191, "invalid upload ACK"),
            (hil.UPLOAD_NACK_MAGIC, 1, "invalid upload NACK"),
            (b"NOPE", 0, "invalid upload response"),
        )

        for magic, target_offset, message in cases:
            with self.subTest(magic=magic, target_offset=target_offset):
                class InvalidResponseSession:
                    def write(self, _chunk, deadline=None):
                        pass

                    def read_exact(self, _size, _timeout):
                        return hil.UPLOAD_ACK.pack(magic, target_offset)

                with tempfile.TemporaryDirectory() as temporary:
                    path = pathlib.Path(temporary) / "payload"
                    path.write_bytes(payload)
                    with mock.patch.object(hil.time, "sleep"):
                        with self.assertRaisesRegex(hil.PythonHilError, message):
                            hil.send_upload_frames(
                                InvalidResponseSession(),
                                path,
                                len(payload),
                                10.0,
                                1.0,
                            )

    def test_upload_response_timeout_is_terminal_without_retransmission(self):
        payload = b"t" * 1500

        class TimeoutSession:
            def __init__(self):
                self.current = bytearray()
                self.reads = 0

            def write(self, chunk, deadline=None):
                self.current.extend(chunk)

            def read_exact(self, _size, _timeout):
                self.reads += 1
                raise hil.PythonHilError("serial read timeout")

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            session = TimeoutSession()
            with mock.patch.object(hil.time, "sleep"):
                with self.assertRaisesRegex(
                    hil.PythonHilError, "serial read timeout"
                ):
                    hil.send_upload_frames(
                        session, path, len(payload), 10.0, 1.0
                    )

        correct_first = next(
            iter(hil.upload_frames(io.BytesIO(payload), len(payload)))
        )[1]
        self.assertEqual(session.reads, 1)
        self.assertEqual(bytes(session.current), correct_first)

    def test_upload_nack_retry_budget_is_fail_closed(self):
        payload = b"z" * 192

        class RejectSession:
            def __init__(self):
                self.transmissions = 0

            def write(self, chunk, deadline=None):
                if chunk.startswith(hil.UPLOAD_FRAME.pack(0, len(payload), binascii.crc32(payload) & 0xffffffff)):
                    self.transmissions += 1

            def read_exact(self, _size, _timeout):
                return hil.UPLOAD_ACK.pack(hil.UPLOAD_NACK_MAGIC, 0)

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            session = RejectSession()
            with mock.patch.object(hil.time, "sleep"):
                with self.assertRaisesRegex(
                    hil.PythonHilError, "after 3 retries"
                ):
                    hil.send_upload_frames(
                        session, path, len(payload), 10.0, 1.0
                    )

        self.assertEqual(session.transmissions, 4)

    def test_execute_persists_partial_evidence_for_every_failure_class(self):
        failures = (
            hil.PythonHilError("protocol failed"),
            OSError("serial disconnected"),
            RuntimeError("unexpected failure"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for index, failure in enumerate(failures):
                with self.subTest(failure=type(failure).__name__):
                    artifact = root / "artifact-{}".format(index)
                    args = types.SimpleNamespace(
                        artifact_dir=artifact,
                        lock_file=root / "board.lock",
                        loadp2=pathlib.Path(sys.executable),
                        serial="/dev/null",
                        baud=hil.RUNTIME_BAUD,
                        loader_baud=2000000,
                        reset_method="dtr",
                        image=root / "nuttx.bin",
                        container=root / "python.p2py",
                        load_timeout=1.0,
                        boot_timeout=1.0,
                        upload_timeout=1.0,
                        test_timeout=1.0,
                    )
                    guard = FakeSerial(b"")
                    connection = FakeSerial(b"")
                    partial_rx = (
                        overlay_stats_line(
                            "SAMPLE",
                            entries=3,
                            exits=1,
                            direct=1,
                            attempts=2,
                            loads=2,
                            load_bytes=0x800,
                            depth=1,
                            maximum=2,
                            group=4,
                            requested_group=4,
                            stub=7,
                        )
                        + b"partial-rx"
                    )

                    def fail(session, *_args, **_kwargs):
                        session.received.extend(partial_rx)
                        session.sent.extend(b"partial-tx")
                        raise failure

                    environment = {
                        "P2_HIL": "1",
                        "P2_ALLOW_RESET": "1",
                        "P2_ALLOW_PSRAM_WRITE": "1",
                    }
                    loaded = types.SimpleNamespace(
                        returncode=0, stdout=b"partial-loader"
                    )
                    with mock.patch.dict(hil.os.environ, environment, clear=False):
                        with mock.patch.object(hil, "run_loader", return_value=loaded):
                            with mock.patch.object(
                                hil,
                                "open_serial",
                                side_effect=(guard, connection),
                            ):
                                with mock.patch.object(
                                    hil, "run_python_tests", side_effect=fail
                                ):
                                    with self.assertRaises(type(failure)):
                                        hil.execute(args, {"validated": True})

                    status = json.loads((artifact / "status.json").read_text())
                    self.assertEqual(status["status"], "FAIL")
                    self.assertEqual(status["failure_type"], type(failure).__name__)
                    self.assertIn(str(failure), status["reason"])
                    self.assertTrue(status["ended_utc"])
                    self.assertEqual(
                        (artifact / "loader.log").read_bytes(), b"partial-loader"
                    )
                    self.assertEqual(
                        (artifact / "serial.raw").read_bytes(), partial_rx
                    )
                    self.assertEqual(
                        (artifact / "serial-tx.raw").read_bytes(), b"partial-tx"
                    )
                    self.assertTrue(status["overlay_telemetry"]["analysis"]["valid"])
                    self.assertFalse(
                        status["overlay_telemetry"]["analysis"][
                            "qualification_valid"
                        ]
                    )
                    self.assertEqual(
                        status["overlay_telemetry"]["analysis"]["record_count"],
                        1,
                    )
                    self.assertEqual(
                        status["overlay_telemetry"]["records"][0]["stage"],
                        "SAMPLE",
                    )
                    self.assertEqual(guard.closes, 1)
                    self.assertEqual(connection.closes, 1)
                    self.assertEqual(
                        status["failure_drain"],
                        {
                            "enabled": False,
                            "quiet_seconds": hil.FAILURE_DRAIN_QUIET_SECONDS,
                            "state": "disabled",
                            "terminal_markers": ["nsh> "],
                            "timeout_seconds": 0.0,
                        },
                    )

    def test_failure_drain_records_raw_rx_until_terminal_marker_is_quiet(self):
        tail = (
            b"Traceback (most recent call last):\r\n"
            b"P2XMEM:FAULT:diagnostic-only\r\n"
            b"ValueError: late failure\r\n"
            b"nsh> "
            b"late diagnostic suffix"
        )
        connection = FakeSerial(tail, read_size=5)
        session = hil.SerialSession(connection)
        progress = []

        def record(phase, details):
            progress.append((phase, dict(details)))

        with mock.patch.object(hil, "FAILURE_DRAIN_QUIET_SECONDS", 0.001):
            result = hil.drain_failure_serial(
                session, 0.2, progress_callback=record
            )

        self.assertEqual(bytes(session.received), tail)
        self.assertEqual(bytes(session.pending), tail)
        self.assertEqual(bytes(session.sent), b"")
        self.assertEqual(bytes(connection.outgoing), b"")
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["stop_reason"], "terminal_marker_quiet")
        self.assertEqual(result["terminal_marker"], "nsh> ")
        self.assertEqual(result["received_bytes"], len(tail))
        self.assertEqual(result["write_bytes"], 0)
        self.assertEqual(progress[0][0], "failure_drain")
        self.assertEqual(progress[0][1]["state"], "draining")
        self.assertEqual(progress[-1][1]["state"], "complete")

    def test_failure_drain_does_not_accept_prompt_text_inside_a_line(self):
        connection = FakeSerial(b"exception text contains nsh> but no prompt")
        session = hil.SerialSession(connection)
        with mock.patch.object(hil, "FAILURE_DRAIN_QUIET_SECONDS", 0.001):
            result = hil.drain_failure_serial(session, 0.01)

        self.assertEqual(result["stop_reason"], "timeout")
        self.assertIsNone(result["terminal_marker"])
        self.assertEqual(bytes(connection.outgoing), b"")

    def test_failure_drain_preserves_line_context_across_scan_truncation(self):
        tail = (
            b"x"
            + b"nsh> "
            + b"y" * hil.FAILURE_DRAIN_SCAN_BYTES
        )
        connection = FakeSerial(tail, read_size=len(tail))
        session = hil.SerialSession(connection)
        with mock.patch.object(hil, "FAILURE_DRAIN_QUIET_SECONDS", 0.001):
            result = hil.drain_failure_serial(session, 0.01)

        self.assertEqual(bytes(session.received), tail)
        self.assertEqual(result["stop_reason"], "timeout")
        self.assertIsNone(result["terminal_marker"])
        self.assertEqual(bytes(connection.outgoing), b"")

    def test_failure_drain_does_not_swallow_keyboard_interrupt(self):
        connection = FakeSerial(b"")
        session = hil.SerialSession(connection)
        with mock.patch.object(
            session, "_receive_raw", side_effect=KeyboardInterrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                hil.drain_failure_serial(session, 0.01)

        self.assertEqual(bytes(session.sent), b"")
        self.assertEqual(bytes(connection.outgoing), b"")

    def test_execute_drains_before_close_and_reraises_original_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            args = types.SimpleNamespace(
                artifact_dir=root / "artifact",
                lock_file=root / "board.lock",
                loadp2=pathlib.Path(sys.executable),
                serial="/dev/null",
                baud=hil.RUNTIME_BAUD,
                loader_baud=2000000,
                reset_method="dtr",
                image=root / "nuttx.bin",
                container=root / "python.p2py",
                load_timeout=1.0,
                boot_timeout=1.0,
                upload_timeout=1.0,
                test_timeout=1.0,
                failure_drain_timeout=0.2,
            )
            tail = (
                b"Traceback (most recent call last):\r\n"
                b"RuntimeError: overlay failure\r\n"
                b"nsh> "
            )
            events = []

            class TrackedSerial(FakeSerial):
                def __init__(self, name, incoming=b""):
                    super().__init__(incoming, read_size=7)
                    self.name = name

                def read(self, size):
                    if self.incoming:
                        events.append("read_{}".format(self.name))
                    return super().read(size)

                def close(self):
                    events.append("close_{}".format(self.name))
                    super().close()

            guard = TrackedSerial("guard")
            connection = TrackedSerial("session", tail)
            original = hil.PythonHilError("original Python failure")
            environment = {
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_PSRAM_WRITE": "1",
            }
            loaded = types.SimpleNamespace(returncode=0, stdout=b"loaded")

            with mock.patch.dict(hil.os.environ, environment, clear=False):
                with mock.patch.object(hil, "run_loader", return_value=loaded):
                    with mock.patch.object(
                        hil,
                        "open_serial",
                        side_effect=(guard, connection),
                    ):
                        with mock.patch.object(
                            hil, "run_python_tests", side_effect=original
                        ):
                            with mock.patch.object(
                                hil, "FAILURE_DRAIN_QUIET_SECONDS", 0.001
                            ):
                                with self.assertRaises(
                                    hil.PythonHilError
                                ) as raised:
                                    hil.execute(args, {"validated": True})

            self.assertIs(raised.exception, original)
            self.assertEqual(
                (args.artifact_dir / "serial.raw").read_bytes(), tail
            )
            self.assertEqual(bytes(connection.outgoing), b"")
            self.assertLess(
                max(
                    index
                    for index, event in enumerate(events)
                    if event == "read_session"
                ),
                events.index("close_session"),
            )
            self.assertLess(
                events.index("close_session"), events.index("close_guard")
            )
            status = json.loads((args.artifact_dir / "status.json").read_text())
            self.assertEqual(status["status"], "FAIL")
            self.assertEqual(status["reason"], str(original))
            self.assertEqual(status["failure_type"], "PythonHilError")
            self.assertTrue(status["failure_drain"]["enabled"])
            self.assertEqual(status["failure_drain"]["state"], "complete")
            self.assertEqual(
                status["failure_drain"]["stop_reason"],
                "terminal_marker_quiet",
            )
            self.assertEqual(
                status["failure_drain"]["received_bytes"], len(tail)
            )
            self.assertEqual(status["failure_drain"]["write_bytes"], 0)
            self.assertEqual(
                status["failure_drain"]["original_failure_reason"],
                str(original),
            )
            phases = [event["phase"] for event in status["progress_history"]]
            self.assertLess(phases.index("failure_drain"), phases.index("failed"))

    def test_execute_exposes_durable_rx_before_the_run_returns(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            args = types.SimpleNamespace(
                artifact_dir=root / "artifact",
                lock_file=root / "board.lock",
                loadp2=pathlib.Path(sys.executable),
                serial="/dev/null",
                baud=hil.RUNTIME_BAUD,
                loader_baud=2000000,
                reset_method="dtr",
                image=root / "nuttx.bin",
                container=root / "python.p2py",
                load_timeout=1.0,
                boot_timeout=1.0,
                upload_timeout=1.0,
                test_timeout=1.0,
            )
            raw = overlay_stats_line(
                "SAMPLE",
                entries=3,
                exits=1,
                direct=1,
                attempts=2,
                loads=2,
                load_bytes=0x800,
                depth=1,
                maximum=2,
                group=4,
                requested_group=4,
                stub=7,
            )
            guard = FakeSerial(b"")
            connection = FakeSerial(raw, read_size=len(raw))
            observed = {}

            def fail_after_snapshot(session, *_args, **_kwargs):
                session._receive(hil.time.monotonic() + 1.0)
                observed.update(
                    hil.read_live_serial_evidence(args.artifact_dir)
                )
                raise hil.PythonHilError("planned live-evidence stop")

            environment = {
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_PSRAM_WRITE": "1",
            }
            loaded = types.SimpleNamespace(returncode=0, stdout=b"loaded")
            with mock.patch.dict(hil.os.environ, environment, clear=False):
                with mock.patch.object(hil, "run_loader", return_value=loaded):
                    with mock.patch.object(
                        hil, "open_serial", side_effect=(guard, connection)
                    ):
                        with mock.patch.object(
                            hil,
                            "run_python_tests",
                            side_effect=fail_after_snapshot,
                        ):
                            with self.assertRaisesRegex(
                                hil.PythonHilError,
                                "planned live-evidence stop",
                            ):
                                hil.execute(args, {"validated": True})

            self.assertEqual(observed["serial_rx"], raw)
            self.assertEqual(
                observed["progress"]["telemetry_record_count"], 1
            )
            self.assertEqual(
                (args.artifact_dir / "serial.raw").read_bytes(), raw
            )
            self.assertEqual(connection.closes, 1)
            self.assertEqual(guard.closes, 1)

    def test_execute_fails_before_serial_if_evidence_cannot_initialize(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            args = types.SimpleNamespace(
                artifact_dir=root / "artifact",
                lock_file=root / "board.lock",
                loadp2=pathlib.Path(sys.executable),
                serial="/dev/null",
                baud=hil.RUNTIME_BAUD,
                loader_baud=2000000,
                reset_method="dtr",
                image=root / "nuttx.bin",
                container=root / "python.p2py",
                load_timeout=1.0,
                boot_timeout=1.0,
                upload_timeout=1.0,
                test_timeout=1.0,
            )
            environment = {
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_PSRAM_WRITE": "1",
            }
            with mock.patch.dict(hil.os.environ, environment, clear=False):
                with mock.patch.object(
                    hil.LiveSerialEvidence,
                    "checkpoint",
                    side_effect=hil.PythonHilError("injected evidence failure"),
                ):
                    with mock.patch.object(hil, "open_serial") as open_serial:
                        with self.assertRaisesRegex(
                            hil.PythonHilError,
                            "injected evidence failure",
                        ):
                            hil.execute(args, {"validated": True})
            open_serial.assert_not_called()
            status = json.loads((args.artifact_dir / "status.json").read_text())
            self.assertEqual(status["status"], "FAIL")
            self.assertIn("injected evidence failure", status["reason"])

    def test_execute_rejects_malformed_telemetry_after_other_tests_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            args = types.SimpleNamespace(
                artifact_dir=root / "artifact",
                lock_file=root / "board.lock",
                loadp2=pathlib.Path(sys.executable),
                serial="/dev/null",
                baud=hil.RUNTIME_BAUD,
                loader_baud=2000000,
                reset_method="dtr",
                image=root / "nuttx.bin",
                container=root / "python.p2py",
                load_timeout=1.0,
                boot_timeout=1.0,
                upload_timeout=1.0,
                test_timeout=1.0,
            )
            guard = FakeSerial(b"")
            connection = FakeSerial(b"")

            def pass_with_bad_telemetry(session, *_args, **_kwargs):
                session.received.extend(
                    overlay_stats_line("LAUNCH")
                    + b"P2PY:OVL:SAMPLE:E=broken\r\n"
                )
                return {"completed_tests": ["mock"]}

            environment = {
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_PSRAM_WRITE": "1",
            }
            loaded = types.SimpleNamespace(returncode=0, stdout=b"loaded")
            with mock.patch.dict(hil.os.environ, environment, clear=False):
                with mock.patch.object(hil, "run_loader", return_value=loaded):
                    with mock.patch.object(
                        hil,
                        "open_serial",
                        side_effect=(guard, connection),
                    ):
                        with mock.patch.object(
                            hil,
                            "run_python_tests",
                            side_effect=pass_with_bad_telemetry,
                        ):
                            with self.assertRaisesRegex(
                                hil.PythonHilError,
                                "overlay telemetry validation failed",
                            ):
                                hil.execute(args, {"validated": True})

            status = json.loads((args.artifact_dir / "status.json").read_text())
            self.assertEqual(status["status"], "FAIL")
            self.assertEqual(status["failure_type"], "PythonHilError")
            self.assertEqual(
                status["overlay_telemetry"]["analysis"]["malformed_count"],
                1,
            )
            self.assertFalse(status["overlay_telemetry"]["analysis"]["valid"])
            self.assertEqual(
                status["overlay_telemetry"]["records"][0]["stage"],
                "LAUNCH",
            )

    def test_execute_rejects_a_mocked_pass_with_no_overlay_telemetry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            args = types.SimpleNamespace(
                artifact_dir=root / "artifact",
                lock_file=root / "board.lock",
                loadp2=pathlib.Path(sys.executable),
                serial="/dev/null",
                baud=hil.RUNTIME_BAUD,
                loader_baud=2000000,
                reset_method="dtr",
                image=root / "nuttx.bin",
                container=root / "python.p2py",
                load_timeout=1.0,
                boot_timeout=1.0,
                upload_timeout=1.0,
                test_timeout=1.0,
            )
            guard = FakeSerial(b"")
            connection = FakeSerial(b"")
            environment = {
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_PSRAM_WRITE": "1",
            }
            loaded = types.SimpleNamespace(returncode=0, stdout=b"loaded")
            with mock.patch.dict(hil.os.environ, environment, clear=False):
                with mock.patch.object(hil, "run_loader", return_value=loaded):
                    with mock.patch.object(
                        hil,
                        "open_serial",
                        side_effect=(guard, connection),
                    ):
                        with mock.patch.object(
                            hil,
                            "run_python_tests",
                            return_value=successful_hil_result(),
                        ):
                            with self.assertRaisesRegex(
                                hil.PythonHilError,
                                "overlay telemetry validation failed",
                            ):
                                hil.execute(args, {"validated": True})

            status = json.loads((args.artifact_dir / "status.json").read_text())
            analysis = status["overlay_telemetry"]["analysis"]
            self.assertEqual(status["status"], "FAIL")
            self.assertTrue(analysis["record_valid"])
            self.assertFalse(analysis["qualification_valid"])
            self.assertEqual(analysis["stats_count"], 0)
            self.assertEqual(analysis["lifecycle_count"], 0)
            self.assertIn(
                "LAUNCH stage count 0 != {}".format(
                    hil.EXPECTED_SUCCESSFUL_WORKERS
                ),
                "\n".join(status["overlay_telemetry"]["qualification_errors"]),
            )

    def test_execute_keeps_guard_open_across_loader_and_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            args = types.SimpleNamespace(
                artifact_dir=root / "artifact",
                lock_file=root / "board.lock",
                loadp2=pathlib.Path(sys.executable),
                serial="/dev/null",
                baud=hil.RUNTIME_BAUD,
                loader_baud=2000000,
                reset_method="dtr",
                image=root / "nuttx.bin",
                container=root / "python.p2py",
                load_timeout=1.0,
                boot_timeout=1.0,
                upload_timeout=1.0,
                test_timeout=1.0,
            )
            events = []
            run_test_arguments = []
            progress_snapshots = []

            class TrackedSerial(FakeSerial):
                def __init__(self, name):
                    super().__init__(b"")
                    self.name = name

                def close(self):
                    events.append("close_{}".format(self.name))
                    super().close()

            guard = TrackedSerial("guard")
            connection = TrackedSerial("session")

            def open_serial(*_args):
                if not events:
                    events.append("open_guard")
                    return guard
                events.append("open_session")
                return connection

            def run_loader(*_args):
                events.append("loader")
                return types.SimpleNamespace(returncode=0, stdout=b"loaded")

            def run_tests(*_args, **_kwargs):
                events.append("tests")
                run_test_arguments.append((_args, _kwargs))
                progress_snapshots.append(
                    json.loads((args.artifact_dir / "status.json").read_text())
                )
                _kwargs["progress_callback"](
                    "runtime_stages",
                    {
                        "state": "waiting",
                        "next_stage": "P2PY:CPYTHON:RUN",
                        "completed": 5,
                        "total": 6,
                        "overall_deadline_seconds": 1.0,
                        "remaining_seconds": 0.5,
                    },
                )
                progress_snapshots.append(
                    json.loads((args.artifact_dir / "status.json").read_text())
                )
                _args[0].received.extend(qualified_serial())
                return successful_hil_result()

            environment = {
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_PSRAM_WRITE": "1",
            }
            with mock.patch.dict(hil.os.environ, environment, clear=False):
                with mock.patch.object(hil, "open_serial", side_effect=open_serial):
                    with mock.patch.object(hil, "run_loader", side_effect=run_loader):
                        with mock.patch.object(
                            hil, "run_python_tests", side_effect=run_tests
                        ):
                            hil.execute(args, {"validated": True})

            self.assertEqual(
                events,
                [
                    "open_guard",
                    "loader",
                    "open_session",
                    "tests",
                    "close_session",
                    "close_guard",
                ],
            )
            status = json.loads((args.artifact_dir / "status.json").read_text())
            self.assertEqual(
                status["serial_handoff_guard"],
                "nonreading-shared-descriptor",
            )
            self.assertEqual(len(run_test_arguments), 1)
            positional, keywords = run_test_arguments[0]
            self.assertIs(positional[-1], True)
            self.assertTrue(callable(keywords["progress_callback"]))
            self.assertEqual(progress_snapshots[0]["current_phase"], "loader")
            self.assertEqual(progress_snapshots[0]["progress"]["state"], "complete")
            self.assertEqual(
                progress_snapshots[1]["current_phase"], "runtime_stages"
            )
            self.assertEqual(progress_snapshots[1]["progress"]["completed"], 5)
            self.assertIn(
                "overall_deadline_utc", progress_snapshots[1]["progress"]
            )
            self.assertEqual(status["current_phase"], "passed")
            self.assertEqual(status["progress"]["state"], "complete")
            sequences = [event["sequence"] for event in status["progress_history"]]
            self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
            for event in status["progress_history"]:
                self.assertTrue(event["updated_utc"])
                self.assertGreaterEqual(event["run_elapsed_seconds"], 0.0)
            self.assertEqual(
                status["expected_successful_workers"],
                hil.EXPECTED_SUCCESSFUL_WORKERS,
            )
            self.assertEqual(
                status["expected_successful_worker_names"],
                list(hil.EXPECTED_SUCCESSFUL_WORKER_NAMES),
            )
            self.assertEqual(
                status["interactive_repl"]["expression_marker"],
                hil.INTERACTIVE_REPL_EXPRESSION_MARKER,
            )
            self.assertEqual(
                status["interactive_repl_contract"]["exit_command"],
                hil.INTERACTIVE_REPL_EXIT_COMMAND,
            )
            self.assertEqual(
                status["overlay_telemetry"]["analysis"]["lifecycle_count"],
                hil.EXPECTED_SUCCESSFUL_WORKERS,
            )
            self.assertEqual(
                status["upload_fault_injection"],
                {
                    "enabled": True,
                    "kinds": list(hil.UPLOAD_FAULT_SEQUENCE),
                },
            )

    def test_execute_smoke_is_explicitly_not_a_full_pass_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            args = types.SimpleNamespace(
                artifact_dir=root / "artifact",
                lock_file=root / "board.lock",
                loadp2=pathlib.Path(sys.executable),
                serial="/dev/null",
                baud=hil.RUNTIME_BAUD,
                loader_baud=2000000,
                reset_method="dtr",
                image=root / "nuttx.bin",
                container=root / "python.p2py",
                load_timeout=1.0,
                boot_timeout=1.0,
                upload_timeout=1.0,
                test_timeout=10.0,
                skip_upload_fault_injection=True,
            )
            guard = FakeSerial(b"")
            connection = FakeSerial(b"")

            def run_tests(session, *_args, **kwargs):
                self.assertIs(kwargs["plan"], hil.SMOKE_QUALIFICATION_PLAN)
                session.received.extend(
                    qualified_serial(
                        plan=hil.SMOKE_QUALIFICATION_PLAN,
                        sample_indices=(0,),
                    )
                )
                return successful_smoke_result()

            environment = {
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_PSRAM_WRITE": "1",
            }
            output = io.StringIO()
            loaded = types.SimpleNamespace(returncode=0, stdout=b"loaded")
            with mock.patch.dict(hil.os.environ, environment, clear=False):
                with mock.patch.object(hil, "run_loader", return_value=loaded):
                    with mock.patch.object(
                        hil, "open_serial", side_effect=(guard, connection)
                    ):
                        with mock.patch.object(
                            hil, "run_python_tests", side_effect=run_tests
                        ):
                            with contextlib.redirect_stdout(output):
                                self.assertEqual(
                                    hil.execute(
                                        args,
                                        {"validated": True},
                                        hil.SMOKE_QUALIFICATION_PLAN,
                                    ),
                                    0,
                                )

            status = json.loads((args.artifact_dir / "status.json").read_text())
            self.assertEqual(status["format"], "p2-python-hil-smoke-v1")
            self.assertEqual(status["status"], "SMOKE_PASS")
            self.assertEqual(status["qualification_level"], "smoke")
            self.assertFalse(status["full_qualification"])
            self.assertEqual(status["expected_successful_workers"], 1)
            self.assertEqual(status["tests"][0]["name"], "arithmetic")
            self.assertEqual(len(status["tests"]), 1)
            self.assertEqual(len(status["omitted_tests"]), len(hil.PYTHON_TESTS) - 1)
            self.assertTrue(status["restart_stress"]["skipped"])
            self.assertTrue(status["concurrency"]["skipped"])
            self.assertTrue(
                status["overlay_telemetry"]["analysis"]["qualification_valid"]
            )
            marker = output.getvalue()
            self.assertIn("P2PYHIL:SMOKE:PASS:NOT_FULL:ARTIFACT=", marker)
            self.assertNotIn("P2PYHIL:PASS:ARTIFACT=", marker)

    def test_cli_rejects_nonruntime_baud_before_preflight_or_hardware(self):
        arguments = (
            "--serial",
            "/dev/cu.board",
            "--baud",
            "115200",
            "--image",
            "/artifacts/nuttx.bin",
            "--resident-elf",
            "/artifacts/nuttx",
            "--container",
            "/artifacts/python.p2py",
            "--artifact-dir",
            "/artifacts/evidence",
        )
        with mock.patch.object(hil, "validate_artifacts") as validate:
            with mock.patch.object(hil, "open_serial") as open_serial:
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        hil.main(arguments)
        self.assertEqual(raised.exception.code, 2)
        validate.assert_not_called()
        open_serial.assert_not_called()

    def test_default_mode_is_dry_run_and_never_opens_serial(self):
        arguments = (
            "--serial",
            "/dev/cu.board",
            "--image",
            "/artifacts/nuttx.bin",
            "--resident-elf",
            "/artifacts/nuttx",
            "--container",
            "/artifacts/python.p2py",
            "--artifact-dir",
            "/artifacts/evidence",
        )
        output = io.StringIO()
        with mock.patch.object(
            hil, "validate_artifacts", return_value={"validated": True}
        ):
            with mock.patch.object(hil, "execute") as execute:
                with mock.patch.object(hil, "open_serial") as open_serial:
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(hil.main(arguments), 0)
        execute.assert_not_called()
        open_serial.assert_not_called()
        self.assertIn("DRY-RUN", output.getvalue())

    def test_smoke_dry_run_prints_exact_loader_and_timeout_provenance(self):
        arguments = (
            "--qualification-level",
            "smoke",
            "--skip-upload-fault-injection",
            "--serial",
            "/dev/cu.board",
            "--loader-baud",
            "2000000",
            "--loadp2",
            "/tools/loadp2",
            "--image",
            "/artifacts/nuttx.bin",
            "--resident-elf",
            "/artifacts/nuttx",
            "--container",
            "/artifacts/python.p2py",
            "--artifact-dir",
            "/artifacts/evidence",
            "--reset-method",
            "dtr",
            "--load-timeout",
            "90",
            "--boot-timeout",
            "120",
            "--upload-timeout",
            "1800",
            "--test-timeout",
            "1800",
            "--failure-drain-timeout",
            "45",
        )
        output = io.StringIO()
        with mock.patch.object(
            hil, "validate_artifacts", return_value={"validated": True}
        ):
            with mock.patch.object(hil, "execute") as execute:
                with mock.patch.object(hil, "open_serial") as open_serial:
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(hil.main(arguments), 0)

        execute.assert_not_called()
        open_serial.assert_not_called()
        encoded_plan, trailer = output.getvalue().split("\nDRY-RUN:", 1)
        plan = json.loads(encoded_plan)
        self.assertIn("no serial open", trailer)
        self.assertEqual(plan["qualification_level"], "smoke")
        self.assertFalse(plan["full_qualification"])
        self.assertEqual(plan["tests"], ["arithmetic"])
        self.assertEqual(len(plan["omitted_tests"]), len(hil.PYTHON_TESTS) - 1)
        self.assertEqual(plan["expected_successful_workers"], 1)
        self.assertFalse(plan["upload_fault_injection"]["enabled_on_execute"])
        self.assertEqual(
            plan["timeouts_seconds"],
            {
                "load": 90.0,
                "boot": 120.0,
                "upload": 1800.0,
                "test": 1800.0,
                "failure_drain": 45.0,
            },
        )
        self.assertEqual(
            plan["loader_command"],
            [
                "/tools/loadp2",
                "-p",
                "/dev/cu.board",
                "-l",
                "2000000",
                "-b",
                "2000000",
                "-ZERO",
                "-v",
                "-DTR",
                "/artifacts/nuttx.bin",
            ],
        )


if __name__ == "__main__":
    unittest.main()
