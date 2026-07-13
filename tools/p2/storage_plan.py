#!/usr/bin/env python3
"""Shared guarded multi-reset runner for P2 storage HIL wrappers."""

import datetime
import json
import os
import pathlib
import secrets
import sys
from typing import Dict, Mapping, Optional, Sequence, Tuple

import hil
from storage_protocol import (
    BOOT_CRC_PATTERN,
    FLASH_WRITABLE_ACTIONS,
    SD_DESTRUCTIVE_ACTIONS,
    normalize_sequence,
    sequence_required,
)


INTERRUPT_RESET_LIMIT_SECONDS = 10.0


def storage_sequence(value: Optional[str]) -> str:
    return normalize_sequence(value) if value else secrets.token_hex(4).upper()


def validate_execution_gates(
    execute: bool,
    actions: Sequence[str],
    environment: Mapping[str, str],
) -> Optional[str]:
    """Return a refusal reason, or ``None`` when every plan gate is open."""

    if not execute:
        return (
            "DRY-RUN: no build, serial open, reset, format, erase, or write "
            "was performed; pass --execute"
        )
    if environment.get("P2_HIL", "0") != "1":
        return "HIL REQUIRED: set P2_HIL=1 before --execute"
    if environment.get("P2_ALLOW_FLASH_WRITE", "0") != "1":
        return (
            "storage HIL requires P2_ALLOW_FLASH_WRITE=1 because W25 "
            "initialization may clear protection bits"
        )
    if any(action in FLASH_WRITABLE_ACTIONS for action in actions):
        if environment.get("P2_ALLOW_FLASH_ERASE", "0") != "1":
            return (
                "flash writable stages require P2_ALLOW_FLASH_WRITE=1 and "
                "P2_ALLOW_FLASH_ERASE=1"
            )
    if any(action in SD_DESTRUCTIVE_ACTIONS for action in actions):
        if environment.get("P2_ALLOW_SD_DESTRUCTIVE", "0") != "1":
            return (
                "destructive SD stages require "
                "P2_ALLOW_SD_DESTRUCTIVE=1"
            )
    return None


def default_artifact_dir(kind: str) -> pathlib.Path:
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        hil.REPO_ROOT
        / "artifacts"
        / "hil"
        / "{}-{}".format(hil.run_stamp(now), kind)
    )


def _cycle_marker_utc(stage_dir: pathlib.Path, label_prefix: str) -> datetime.datetime:
    cycle = stage_dir / "cycle-001"
    metadata = json.loads((cycle / "metadata.json").read_text(encoding="utf-8"))
    markers = json.loads((cycle / "markers.json").read_text(encoding="utf-8"))
    started = datetime.datetime.fromisoformat(
        metadata["started_utc"].replace("Z", "+00:00")
    )
    observed = markers["observed_after_start_seconds"]
    matches = [
        float(seconds)
        for label, seconds in observed.items()
        if label.startswith(label_prefix)
    ]
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one {} marker timestamp in {}".format(
                label_prefix, stage_dir
            )
        )
    return started + datetime.timedelta(seconds=matches[0])


def interrupt_reset_elapsed(
    arm_stage: pathlib.Path, verify_stage: pathlib.Path
) -> float:
    """Measure READY-to-next-P2BOOT from preserved per-stage evidence."""

    armed = _cycle_marker_utc(
        arm_stage, "P2STORAGE:READY:POWER-CUT=FLASH:SEQUENCE="
    )
    reset = _cycle_marker_utc(verify_stage, "P2BOOT:ENTRY")
    elapsed = (reset - armed).total_seconds()
    if elapsed < -0.010:
        raise ValueError("interrupt reset timestamp precedes the READY marker")
    return max(0.0, elapsed)


def stage_boot_crc32(stage_dir: pathlib.Path) -> str:
    """Read the one exact boot-reservation CRC emitted during a stage."""

    text = (stage_dir / "cycle-001" / "console.raw").read_bytes().decode(
        "utf-8", "replace"
    )
    matches = list(BOOT_CRC_PATTERN.finditer(text))
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one W25 boot CRC marker in {}".format(stage_dir)
        )
    return matches[0].group("boot_crc32")


def run_plan(
    *,
    kind: str,
    actions: Sequence[str],
    sequence: str,
    artifact_dir: Optional[str],
    image: Optional[str],
    port: Optional[str],
    no_build: bool,
    timeout: float,
    execute: bool,
    environment: Optional[Mapping[str, str]] = None,
) -> int:
    """Run one action per RAM-load/reset and preserve a top-level manifest."""

    env = (
        hil.local_environment(os.environ)
        if environment is None
        else dict(environment)
    )
    refusal = validate_execution_gates(execute, actions, env)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return hil.EXIT_SAFETY

    sequence_text = normalize_sequence(sequence)
    top = (
        pathlib.Path(artifact_dir).expanduser().resolve()
        if artifact_dir
        else default_artifact_dir(kind)
    )
    if top.exists():
        print("artifact directory already exists: {}".format(top), file=sys.stderr)
        return hil.EXIT_SAFETY
    top.mkdir(parents=True)

    metadata: Dict[str, object] = {
        "status": "RUNNING",
        "kind": kind,
        "sequence": sequence_text,
        "actions": list(actions),
        "automatic_format": False,
        "format_requested": any(action.endswith("-format") for action in actions),
        "persistence_boundary": (
            "each action starts with a fresh loadp2 RAM load and target reset"
        ),
        "gates": {
            "P2_HIL": True,
            "P2_ALLOW_FLASH_WRITE": True,
            "P2_ALLOW_FLASH_ERASE": any(
                action in FLASH_WRITABLE_ACTIONS for action in actions
            ),
            "P2_ALLOW_SD_DESTRUCTIVE": any(
                action in SD_DESTRUCTIVE_ACTIONS for action in actions
            ),
        },
        "stage_artifacts": [],
    }
    hil.write_json(top / "metadata.json", metadata)

    passed = 0
    result = hil.EXIT_OK
    boot_crc32: Optional[str] = None
    try:
        for index, action in enumerate(actions, 1):
            stage_name = "{:02d}-{}".format(index, action)
            stage_dir = top / stage_name
            arguments = [
                "--execute",
                "--protocol",
                "storage",
                "--cycles",
                "1",
                "--timeout",
                str(timeout),
                "--storage-action",
                action,
                "--artifact-dir",
                str(stage_dir),
            ]
            if sequence_required(action):
                arguments.extend(("--storage-sequence", sequence_text))
            if image:
                arguments.extend(("--image", image))
            if port:
                arguments.extend(("--port", port))
            if index == 1 and not no_build:
                arguments.append("--build-standalone")

            metadata["stage_artifacts"].append(
                {
                    "action": action,
                    "directory": stage_name,
                    "reset_stage": index,
                }
            )
            hil.write_json(top / "metadata.json", metadata)
            result = hil.main(arguments, env=env)
            if result != hil.EXIT_OK:
                metadata["failure_action"] = action
                metadata["failure_exit_code"] = result
                break
            observed_boot_crc32 = stage_boot_crc32(stage_dir)
            if boot_crc32 is None:
                boot_crc32 = observed_boot_crc32
                metadata["boot_crc32_before"] = boot_crc32
            elif observed_boot_crc32 != boot_crc32:
                metadata["failure_action"] = action
                metadata["failure_reason"] = (
                    "boot reservation CRC changed: {} -> {}"
                ).format(boot_crc32, observed_boot_crc32)
                result = hil.EXIT_HIL_FAILURE
                break
            metadata["boot_crc32_after_{}".format(action)] = (
                observed_boot_crc32
            )
            if action == "flash-interrupt-verify":
                arm_name = "{:02d}-flash-interrupt-arm".format(index - 1)
                elapsed = interrupt_reset_elapsed(top / arm_name, stage_dir)
                metadata["interrupt_reset_elapsed_seconds"] = round(elapsed, 6)
                metadata["interrupt_reset_limit_seconds"] = (
                    INTERRUPT_RESET_LIMIT_SECONDS
                )
                if elapsed > INTERRUPT_RESET_LIMIT_SECONDS:
                    metadata["failure_action"] = action
                    metadata["failure_reason"] = (
                        "interrupted-write reset exceeded {:.1f}s: {:.6f}s"
                    ).format(INTERRUPT_RESET_LIMIT_SECONDS, elapsed)
                    result = hil.EXIT_HIL_FAILURE
                    break
            passed += 1
    except (OSError, RuntimeError, ValueError) as exc:
        metadata["failure_action"] = actions[passed] if passed < len(actions) else None
        metadata["failure_reason"] = hil.monitor.safe_error(exc)
        result = hil.EXIT_HIL_FAILURE
    finally:
        metadata["actions_passed"] = passed
        metadata["status"] = "PASS" if passed == len(actions) else "FAIL"
        hil.write_json(top / "status.json", metadata)

    print("P2 {} HIL artifact: {}".format(kind, top))
    return result if passed != len(actions) else hil.EXIT_OK
