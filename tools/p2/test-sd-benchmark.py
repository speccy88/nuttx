#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Capture a guarded, non-destructive P2 raw microSD speed measurement.

Serial-only live mode opens an explicitly selected console and sends one
read-only ``p2storage sd-benchmark-read`` command.  The optional ``--ram-load``
mode accepts one clean, sealed ``sdio-record`` build artifact and uses one
pinned ``loadp2`` terminal process to RAM-load its exact ELF, wait for NSH, and
send that same command.  Nothing here flashes or writes the card.  Without
``--execute`` it is a dry-run.
"""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import platform
import re
import secrets
import shlex
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hil  # noqa: E402
import build_artifact  # noqa: E402
import monitor  # noqa: E402
import sd_benchmark_protocol as protocol  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "hil"
DEFAULT_TIMEOUT = 600.0
LOADP2_FIFO_BYTES = 16384
NSH_READY_TIMEOUT = 30.0
BOUND_BOARD = "p2-ec32mb"
BOUND_PROFILE = "sdio-record"
EXIT_OK = 0
EXIT_PROOF_FAILED = 1
EXIT_SAFETY = 2
EXIT_IMAGE_UNVERIFIED = 3
EXIT_LOCK_BUSY = 9

BOUND_REQUIRED_CONFIG = (
    ("CONFIG_ARCH_P2", "y"),
    ("CONFIG_ARCH_BOARD_P2_EC32MB", "y"),
    ("CONFIG_NSH_BUILTIN_APPS", "y"),
    ("CONFIG_P2_EXPERIMENTAL_OVERCLOCK", "y"),
    ("CONFIG_P2_SYSCLK_HZ", "360000000"),
    ("CONFIG_P2_EC32MB_SDIO_NATIVE", "y"),
    ("CONFIG_P2_EC32MB_SDIO_DIVISOR", "3"),
    ("CONFIG_P2_EC32MB_SDIO_ALLOW_OVERCLOCK", "y"),
    ("CONFIG_P2_EC32MB_SDIO_VERIFY_FAST_CRC16", "n"),
    ("CONFIG_MMCSD_SDIO", "y"),
    ("CONFIG_MMCSD_READONLY", "y"),
    ("CONFIG_TESTING_P2STORAGE", "y"),
    ("CONFIG_TESTING_P2STORAGE_SD_BENCHMARK", "y"),
    ("CONFIG_TESTING_P2STORAGE_BENCHMARK_BUFFER_SIZE", "65536"),
    ("CONFIG_TESTING_P2STORAGE_BENCHMARK_DRIVER", '"P2-SDIO-STREAMER"'),
    ("CONFIG_TESTING_P2STORAGE_DESTRUCTIVE", "n"),
)


class SafetyError(ValueError):
    """A RAM-load proof gate or immutable input is invalid."""


class BoundRunError(RuntimeError):
    """The one-session RAM-load benchmark did not complete."""


@dataclass(frozen=True)
class BoundLoadConfig:
    port: str
    build_artifact: pathlib.Path
    build_status: pathlib.Path
    image: pathlib.Path
    generated_config: pathlib.Path
    loadp2: pathlib.Path
    toolchain_lock: pathlib.Path
    artifact_dir: pathlib.Path
    board_lock: pathlib.Path
    loader_baud: int
    console_baud: int
    reset_flag: str
    timeout: float
    lock_timeout: float
    image_sha256: str
    config_sha256: str
    loadp2_sha256: str
    toolchain_lock_sha256: str
    build_status_sha256: str
    nuttx_commit: str
    apps_commit: str
    expected_sysclk_hz: int
    expected_divisor: int
    expected_bus_clock_hz: int
    expected_raw_ceiling_bps: int
    expected_buffer_bytes: int
    expected_driver: str

    def validate(self) -> None:
        if not self.port or not pathlib.Path(self.port).is_absolute():
            raise SafetyError("an absolute serial port is required")
        if not self.build_artifact.is_dir():
            raise SafetyError(
                "sealed build artifact is unavailable: {}".format(self.build_artifact)
            )
        if not self.build_status.is_file():
            raise SafetyError(
                "build artifact status is unavailable: {}".format(self.build_status)
            )
        if not self.image.is_file() or self.image.stat().st_size == 0:
            raise SafetyError(
                "benchmark image is missing or empty: {}".format(self.image)
            )
        with self.image.open("rb") as source:
            if source.read(4) != b"\x7fELF":
                raise SafetyError(
                    "benchmark image is not an ELF file: {}".format(self.image)
                )
        if not self.generated_config.is_file():
            raise SafetyError(
                "generated .config is unavailable: {}".format(self.generated_config)
            )
        if not self.loadp2.is_file() or not os.access(self.loadp2, os.X_OK):
            raise SafetyError("pinned LOADP2 is unavailable: {}".format(self.loadp2))
        if not self.toolchain_lock.is_file():
            raise SafetyError(
                "toolchain lock is unavailable: {}".format(self.toolchain_lock)
            )
        if self.artifact_dir.exists():
            raise SafetyError(
                "artifact directory already exists: {}".format(self.artifact_dir)
            )
        if self.loader_baud <= 0 or self.console_baud <= 0:
            raise SafetyError("loader and console baud must be positive")
        if self.reset_flag not in ("-DTR", "-RTS"):
            raise SafetyError("RAM-load proof requires exactly one DTR or RTS reset")
        if self.timeout <= 0 or self.timeout > 3600:
            raise SafetyError("--timeout must be in (0, 3600]")
        if self.lock_timeout < 0:
            raise SafetyError("lock timeout cannot be negative")
        if self.expected_sysclk_hz <= 0 or self.expected_divisor <= 0:
            raise SafetyError("bound record clock configuration is invalid")
        if self.expected_bus_clock_hz != (
            self.expected_sysclk_hz // self.expected_divisor
        ):
            raise SafetyError("bound record divisor does not produce its SD clock")
        if self.expected_raw_ceiling_bps != self.expected_bus_clock_hz * 4 // 8:
            raise SafetyError("bound record raw ceiling is inconsistent")
        if self.expected_buffer_bytes <= 0 or not self.expected_driver:
            raise SafetyError("bound record benchmark identity is invalid")
        verify_bound_inputs(self)


@dataclass(frozen=True)
class BoundCapture:
    text: str
    prompt_seen: bool
    command_sent: bool
    done_seen: bool
    loader_returncode: Optional[int]
    intentionally_terminated: bool
    failure: Optional[str]
    safety_failure: bool
    preserved_inputs: Dict[str, Dict[str, object]]


def utc_timestamp(now: Optional[datetime.datetime] = None) -> str:
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return (
        now.astimezone(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def artifact_stamp(now: Optional[datetime.datetime] = None) -> str:
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%S.%fZ-sd-benchmark"
    )


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_bound_config(values: Mapping[str, str]) -> None:
    mismatches = [
        "{}={} (required {})".format(name, values.get(name, "<unset>"), expected)
        for name, expected in BOUND_REQUIRED_CONFIG
        if values.get(name) != expected
    ]
    if mismatches:
        raise SafetyError(
            "image does not match the locked native-SD record profile: {}".format(
                ", ".join(mismatches)
            )
        )


def _bound_kconfig_integer(values: Mapping[str, str], name: str) -> int:
    try:
        value = int(values[name], 0)
    except (KeyError, ValueError) as exc:
        raise SafetyError(
            "{} must be an integer in the build config".format(name)
        ) from exc
    if value <= 0:
        raise SafetyError("{} must be positive in the build config".format(name))
    return value


def _bound_kconfig_string(values: Mapping[str, str], name: str) -> str:
    raw = values.get(name, "")
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SafetyError(
            "{} must be a quoted build-config string".format(name)
        ) from exc
    if not isinstance(value, str) or not value:
        raise SafetyError("{} must be a non-empty build-config string".format(name))
    return value


def verify_bound_inputs(config: BoundLoadConfig) -> None:
    checks = (
        ("build artifact status", config.build_status, config.build_status_sha256),
        ("benchmark image", config.image, config.image_sha256),
        ("generated .config", config.generated_config, config.config_sha256),
        ("LOADP2", config.loadp2, config.loadp2_sha256),
        ("toolchain lock", config.toolchain_lock, config.toolchain_lock_sha256),
    )
    for label, path, expected in checks:
        try:
            actual = sha256_file(path)
        except OSError as exc:
            raise SafetyError("{} became unavailable: {}".format(label, exc)) from exc
        if actual != expected:
            raise SafetyError("{} changed after validation".format(label))


def validate_bound_result(
    result: Dict[str, object], config: BoundLoadConfig
) -> Dict[str, object]:
    """Cross-check target telemetry against the sealed build configuration."""

    telemetry = result.get("config")
    errors = []
    if not isinstance(telemetry, dict):
        errors.append("bound target did not report benchmark configuration")
    else:
        expected = {
            "sysclk_hz": config.expected_sysclk_hz,
            "requested_bus_clock_hz": config.expected_bus_clock_hz,
            "bus_clock_hz": config.expected_bus_clock_hz,
            "active_divisor": config.expected_divisor,
            "raw_ceiling_bps": config.expected_raw_ceiling_bps,
            "buffer_bytes": config.expected_buffer_bytes,
            "driver": config.expected_driver,
        }
        for name, value in expected.items():
            if telemetry.get(name) != value:
                errors.append(
                    "bound CONFIG {} is {!r}, build requires {!r}".format(
                        name, telemetry.get(name), value
                    )
                )

        build = telemetry.get("build")
        if (
            not isinstance(build, str)
            or re.fullmatch(r"[0-9a-f]{7,40}", build) is None
            or not config.nuttx_commit.startswith(build)
        ):
            errors.append(
                "bound CONFIG build {!r} does not identify NuttX commit {}".format(
                    build, config.nuttx_commit
                )
            )

    if not errors:
        return result

    checked = dict(result)
    checked["complete"] = False
    checked["status"] = "FAIL"
    checked["errors"] = list(result.get("errors", ())) + errors
    return checked


def evidence_result(
    result: Dict[str, object], target_image_loaded_by_this_tool: bool
) -> Dict[str, object]:
    """Add explicit measurement/proof state without overloading parser PASS."""

    output = dict(result)
    measurement_complete = result.get("complete") is True
    proof_complete = measurement_complete and target_image_loaded_by_this_tool
    if proof_complete:
        evidence_status = "PASS"
    elif measurement_complete:
        evidence_status = "MEASUREMENT_PASS_IMAGE_UNVERIFIED"
    else:
        evidence_status = "FAIL"
    output["protocol_status"] = result.get("status")
    output["status"] = evidence_status
    output["measurement_complete"] = measurement_complete
    output["proof_complete"] = proof_complete
    output["evidence_status"] = evidence_status
    return output


def build_load_command(config: BoundLoadConfig) -> Tuple[str, ...]:
    command = (
        str(config.loadp2),
        "-p",
        config.port,
        "-l",
        str(config.loader_baud),
        "-b",
        str(config.console_baud),
        "-FIFO",
        str(LOADP2_FIFO_BYTES),
        "-ZERO",
        "-v",
        config.reset_flag,
        "-t",
        str(config.image),
    )
    if command.count("-DTR") + command.count("-RTS") != 1:
        raise SafetyError("loadp2 command must contain exactly one reset flag")
    if {"-PATCH", "-FLASH"}.intersection(command):
        raise SafetyError("flash option entered the RAM-only benchmark command")
    if command[0] != str(config.loadp2) or command[-1] != str(config.image):
        raise SafetyError("loadp2 command path or image changed unexpectedly")
    return command


def bound_config_from_args(
    args,
    env: Mapping[str, str],
    port_validator: Callable[[str], bool],
) -> BoundLoadConfig:
    env_port = env.get("P2_PORT", "")
    if not env_port:
        raise SafetyError("P2_PORT must name the exact serial device")
    port = args.port or env_port
    if port != env_port:
        raise SafetyError("--port must exactly match P2_PORT")
    if not pathlib.Path(port).is_absolute():
        raise SafetyError("P2_PORT must be an absolute device path")
    if not port_validator(port):
        raise SafetyError(
            "serial device is absent or not a character device: {}".format(port)
        )

    if args.build_artifact is None:
        raise SafetyError("--ram-load requires a clean --build-artifact")
    try:
        sealed = build_artifact.load(args.build_artifact, require_clean=True)
    except (OSError, build_artifact.BuildArtifactError) as exc:
        raise SafetyError("invalid sealed build artifact: {}".format(exc)) from exc
    if sealed.board != BOUND_BOARD:
        raise SafetyError(
            "benchmark build board is {}, required {}".format(sealed.board, BOUND_BOARD)
        )
    if sealed.profile != BOUND_PROFILE:
        raise SafetyError(
            "benchmark build profile is {}, required {}".format(
                sealed.profile, BOUND_PROFILE
            )
        )

    image = sealed.path / "nuttx"
    generated_config = sealed.path / "config"
    toolchain_lock = sealed.path / "toolchain.lock"
    build_status = sealed.status_path
    try:
        image_sha = sha256_file(image)
        if image_sha != sealed.elf_sha256:
            raise SafetyError("build artifact ELF digest changed after validation")
        config_sha = sha256_file(generated_config)
        lock_sha = sha256_file(toolchain_lock)
        build_status_sha = sha256_file(build_status)
        if build_status_sha != sealed.status_sha256:
            raise SafetyError("build artifact status changed after validation")
        values = hil.read_kconfig(generated_config)
        if sha256_file(generated_config) != config_sha:
            raise SafetyError("generated .config changed during profile validation")
    except (OSError, hil.SafetyError) as exc:
        raise SafetyError("benchmark input is unavailable: {}".format(exc)) from exc
    validate_bound_config(values)

    expected_sysclk_hz = _bound_kconfig_integer(values, "CONFIG_P2_SYSCLK_HZ")
    expected_divisor = _bound_kconfig_integer(values, "CONFIG_P2_EC32MB_SDIO_DIVISOR")
    expected_bus_clock_hz = expected_sysclk_hz // expected_divisor
    expected_raw_ceiling_bps = expected_bus_clock_hz * 4 // 8
    expected_buffer_bytes = _bound_kconfig_integer(
        values, "CONFIG_TESTING_P2STORAGE_BENCHMARK_BUFFER_SIZE"
    )
    expected_driver = _bound_kconfig_string(
        values, "CONFIG_TESTING_P2STORAGE_BENCHMARK_DRIVER"
    )
    if sealed.board_clock_hz != expected_sysclk_hz:
        raise SafetyError(
            "build artifact clock {} disagrees with config {}".format(
                sealed.board_clock_hz, expected_sysclk_hz
            )
        )

    loadp2_text = env.get("LOADP2", "")
    if not loadp2_text:
        raise SafetyError("LOADP2 must name the pinned loader executable")
    loadp2 = pathlib.Path(loadp2_text).expanduser()
    if not loadp2.is_absolute():
        raise SafetyError("LOADP2 must be an absolute path")
    try:
        loadp2 = loadp2.resolve(strict=True)
        loadp2_sha = hil.pinned_sha256(loadp2, toolchain_lock)
        if sha256_file(toolchain_lock) != lock_sha:
            raise SafetyError("sealed toolchain lock changed during pin validation")
    except (OSError, hil.SafetyError) as exc:
        raise SafetyError("pinned loader is unavailable: {}".format(exc)) from exc
    if not loadp2.is_file() or not os.access(loadp2, os.X_OK):
        raise SafetyError("pinned LOADP2 is not executable: {}".format(loadp2))
    if image == loadp2:
        raise SafetyError("P2 image cannot be the LOADP2 executable")

    reset_method = env.get("P2_RESET_METHOD", "loadp2").lower()
    if reset_method in ("loadp2", "dtr"):
        reset_flag = "-DTR"
    elif reset_method == "rts":
        reset_flag = "-RTS"
    else:
        raise SafetyError("P2_RESET_METHOD must be loadp2, dtr, or rts")

    try:
        loader_baud = int(env.get("P2_LOADER_BAUD", "2000000"), 0)
        console_baud = int(env.get("P2_CONSOLE_BAUD", str(args.baud)), 0)
    except ValueError as exc:
        raise SafetyError(
            "cannot validate RAM-load proof inputs: {}".format(exc)
        ) from exc

    artifact_dir = (
        args.artifact_dir.expanduser().resolve()
        if args.artifact_dir is not None
        else DEFAULT_ARTIFACT_ROOT / artifact_stamp()
    )
    board_lock = pathlib.Path(args.lock_file).expanduser().resolve()
    config = BoundLoadConfig(
        port=port,
        build_artifact=sealed.path,
        build_status=build_status,
        image=image,
        generated_config=generated_config,
        loadp2=loadp2,
        toolchain_lock=toolchain_lock,
        artifact_dir=artifact_dir,
        board_lock=board_lock,
        loader_baud=loader_baud,
        console_baud=console_baud,
        reset_flag=reset_flag,
        timeout=args.timeout,
        lock_timeout=args.lock_timeout,
        image_sha256=image_sha,
        config_sha256=config_sha,
        loadp2_sha256=loadp2_sha,
        toolchain_lock_sha256=lock_sha,
        build_status_sha256=build_status_sha,
        nuttx_commit=sealed.nuttx_commit,
        apps_commit=sealed.apps_commit,
        expected_sysclk_hz=expected_sysclk_hz,
        expected_divisor=expected_divisor,
        expected_bus_clock_hz=expected_bus_clock_hz,
        expected_raw_ceiling_bps=expected_raw_ceiling_bps,
        expected_buffer_bytes=expected_buffer_bytes,
        expected_driver=expected_driver,
    )
    config.validate()
    return config


def _preserve_bound_inputs(
    config: BoundLoadConfig,
) -> Dict[str, Dict[str, object]]:
    input_dir = config.artifact_dir / "bound-inputs"
    input_dir.mkdir()
    sources = (
        (
            "build-status.json",
            config.build_status,
            config.build_status_sha256,
        ),
        ("nuttx", config.image, config.image_sha256),
        (".config", config.generated_config, config.config_sha256),
        ("loadp2", config.loadp2, config.loadp2_sha256),
        ("toolchain.lock", config.toolchain_lock, config.toolchain_lock_sha256),
    )
    result: Dict[str, Dict[str, object]] = {}
    for name, source, expected_sha in sources:
        destination = input_dir / name
        shutil.copy2(str(source), str(destination))
        actual_sha = sha256_file(destination)
        if actual_sha != expected_sha:
            raise SafetyError(
                "preserved {} does not match validated input".format(name)
            )
        relative = str(destination.relative_to(config.artifact_dir))
        result[relative] = {
            "bytes": destination.stat().st_size,
            "sha256": actual_sha,
            "source": str(source),
        }
    return dict(sorted(result.items()))


def write_json(path: pathlib.Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _stop_loadp2_session(session) -> Tuple[Optional[int], bool]:
    if session is None:
        return None, False
    intentionally_terminated = False
    try:
        returncode = session.poll()
        if returncode is None:
            intentionally_terminated = True
            session.terminate()
            try:
                returncode = session.wait(timeout=1.0)
            except Exception:
                session.kill()
                returncode = session.wait(timeout=1.0)
        return returncode, intentionally_terminated
    finally:
        session.close()


def run_bound_capture(
    config: BoundLoadConfig,
    benchmark_command: str,
    done_marker: str,
    *,
    process_factory: Callable[[Sequence[str]], object] = hil.default_process_factory,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
        datetime.timezone.utc
    ),
    lock_factory: Callable[..., object] = monitor.BoardLock,
    owner_probe: Callable[[str], Tuple[int, ...]] = hil.default_owner_probe,
) -> BoundCapture:
    """RAM-load and capture through one loadp2 terminal ownership session."""

    config.validate()
    config.artifact_dir.mkdir(parents=True, exist_ok=False)
    preserved_inputs = _preserve_bound_inputs(config)
    load_command = build_load_command(config)
    write_json(
        config.artifact_dir / "loadp2-command.json",
        {"argv": list(load_command), "shell_escaped": shlex.join(load_command)},
    )

    raw_log_path = config.artifact_dir / "console.raw"
    normalized_log_path = config.artifact_dir / "console.log"
    stream = monitor.SerialTextStream()
    transcript = []
    session = None
    loader_returncode = None
    intentionally_terminated = False
    prompt_seen = False
    command_sent = False
    done_seen = False
    failure = None
    safety_failure = False
    prompt_pattern = re.compile(hil.NSH_PROMPT_LINE_PATTERN, re.MULTILINE)
    started = monotonic()
    prompt_deadline = started + min(NSH_READY_TIMEOUT, config.timeout)

    try:
        with lock_factory(
            config.board_lock,
            timeout=config.lock_timeout,
            monotonic=monotonic,
        ):
            owners = owner_probe(config.port)
            if owners:
                raise SafetyError(
                    "serial port is already owned by PID(s): {}".format(
                        ", ".join(str(owner) for owner in owners)
                    )
                )
            verify_bound_inputs(config)
            with raw_log_path.open("wb") as raw_log, normalized_log_path.open(
                "w", encoding="utf-8", newline="\n"
            ) as normalized_log:
                session = process_factory(load_command)
                deadline = started + config.timeout
                while True:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise BoundRunError("bounded timeout before benchmark DONE")
                    chunk = session.read(min(0.10, remaining))
                    if chunk is None:
                        loader_returncode = session.poll()
                        raise BoundRunError(
                            "loadp2 terminal disconnected before benchmark completed"
                        )
                    if chunk:
                        if not isinstance(chunk, (bytes, bytearray)):
                            raise BoundRunError(
                                "loadp2 output reader returned non-bytes"
                            )
                        data = bytes(chunk)
                        raw_log.write(data)
                        raw_log.flush()
                        decoded, lines = stream.feed(data)
                        transcript.append(decoded)
                        stamp = utc_timestamp(utc_now())
                        for line in lines:
                            normalized_log.write("[{}] {}\n".format(stamp, line))
                        normalized_log.flush()

                        text = "".join(transcript)
                        if not prompt_seen and prompt_pattern.search(text):
                            prompt_seen = True
                            verify_bound_inputs(config)
                            session.write((benchmark_command + "\r").encode("ascii"))
                            command_sent = True
                        if command_sent and done_marker in text:
                            done_seen = True
                            verify_bound_inputs(config)
                            break

                    returncode = session.poll()
                    if returncode is not None:
                        loader_returncode = returncode
                        raise BoundRunError(
                            "loadp2 exited with code {} before benchmark completed".format(
                                returncode
                            )
                        )
                    if not prompt_seen and monotonic() >= prompt_deadline:
                        raise BoundRunError("NSH prompt was not seen after RAM load")
    except (SafetyError, hil.SafetyError, monitor.ConfigurationError) as exc:
        failure = monitor.safe_error(exc)
        safety_failure = True
    except (BoundRunError, OSError, RuntimeError, ValueError) as exc:
        failure = monitor.safe_error(exc)
    finally:
        try:
            tail, lines = stream.finish()
            transcript.append(tail)
            if normalized_log_path.is_file() and lines:
                with normalized_log_path.open("a", encoding="utf-8") as normalized_log:
                    stamp = utc_timestamp(utc_now())
                    for line in lines:
                        normalized_log.write("[{}] {}\n".format(stamp, line))
        except (OSError, RuntimeError, ValueError):
            pass
        try:
            stopped_returncode, stopped = _stop_loadp2_session(session)
            if loader_returncode is None:
                loader_returncode = stopped_returncode
            intentionally_terminated = stopped
        except (OSError, RuntimeError, ValueError) as exc:
            if failure is None:
                failure = "cannot stop loadp2 session: {}".format(
                    monitor.safe_error(exc)
                )
        if raw_log_path.is_file():
            shutil.copy2(
                str(raw_log_path),
                str(config.artifact_dir / "loadp2-transcript.raw"),
            )

    return BoundCapture(
        text="".join(transcript),
        prompt_seen=prompt_seen,
        command_sent=command_sent,
        done_seen=done_seen,
        loader_returncode=loader_returncode,
        intentionally_terminated=intentionally_terminated,
        failure=failure,
        safety_failure=safety_failure,
        preserved_inputs=preserved_inputs,
    )


def _preserve_inputs(artifact_dir: pathlib.Path) -> Dict[str, Dict[str, object]]:
    """Copy available local evidence without claiming that this tool loaded it."""

    input_dir = artifact_dir / "inputs"
    input_dir.mkdir(exist_ok=True)
    candidates = (
        pathlib.Path(__file__).resolve(),
        pathlib.Path(protocol.__file__).resolve(),
        REPO_ROOT / ".config",
        REPO_ROOT / "System.map",
        REPO_ROOT / "nuttx",
        REPO_ROOT / "nuttx.bin",
        REPO_ROOT / "tools/p2/toolchain.lock",
    )
    result: Dict[str, Dict[str, object]] = {}
    used = set()
    for source in candidates:
        if not source.is_file() or source.stat().st_size == 0:
            continue
        name = source.name
        if name in used:
            name = "{}-{}".format(source.parent.name, name)
        if name in used:
            raise RuntimeError("duplicate benchmark evidence input {}".format(name))
        used.add(name)
        destination = input_dir / name
        shutil.copy2(str(source), str(destination))
        relative = str(destination.relative_to(artifact_dir))
        result[relative] = {
            "bytes": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "source": str(source),
        }
    return dict(sorted(result.items()))


def _write_artifact(
    artifact_dir: pathlib.Path,
    raw_log: pathlib.Path,
    result: Dict[str, object],
    metadata: Dict[str, object],
) -> Dict[str, object]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    metadata = dict(metadata)
    image_bound = metadata.get("target_image_loaded_by_this_tool") is True
    evidence = evidence_result(result, image_bound)
    measurement_complete = evidence["measurement_complete"]
    proof_complete = evidence["proof_complete"]
    evidence_status = evidence["evidence_status"]

    metadata["measurement_complete"] = measurement_complete
    metadata["proof_complete"] = proof_complete
    metadata["evidence_status"] = evidence_status
    metadata["preserved_inputs"] = _preserve_inputs(artifact_dir)
    if raw_log.is_file():
        metadata["console_raw"] = {
            "bytes": raw_log.stat().st_size,
            "sha256": sha256_file(raw_log),
        }
    write_json(artifact_dir / "result.json", evidence)
    write_json(artifact_dir / "metadata.json", metadata)
    write_json(
        artifact_dir / "status.json",
        {
            "status": evidence_status,
            "measurement_complete": measurement_complete,
            "proof_complete": proof_complete,
            "sequence": evidence["sequence"],
            "threshold_bps": evidence["threshold_bps"],
            "aggregates": evidence["aggregates"],
            "errors": evidence["errors"],
            "duplicates": evidence["duplicates"],
            "failures": evidence["failures"],
            "ended_utc": metadata["ended_utc"],
        },
    )
    return evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure raw P2 microSD reads against a strict 41,000,000 B/s "
            "gate on every pass; final proof also requires image binding"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="open serial and send the read-only command (also requires P2_HIL=1)",
    )
    parser.add_argument(
        "--ram-load",
        action="store_true",
        help=(
            "RAM-load and bind the exact image with pinned loadp2 before the "
            "read-only command; also requires P2_ALLOW_RESET=1"
        ),
    )
    parser.add_argument(
        "--build-artifact",
        type=pathlib.Path,
        help=(
            "clean PASS build artifact for p2-ec32mb:sdio-record; its manifested "
            "ELF, config, and toolchain lock are the only RAM-load inputs"
        ),
    )
    parser.add_argument(
        "--parse-log",
        type=pathlib.Path,
        help="strictly validate an existing console log without touching hardware",
    )
    parser.add_argument("--sequence", help="exact 8-uppercase-hex run nonce")
    parser.add_argument(
        "--bytes",
        type=int,
        dest="byte_count",
        help="bytes per timed pass (record default: 268435456)",
    )
    parser.add_argument("--passes", type=int, help="odd pass count (record default: 7)")
    parser.add_argument("--port", default=os.getenv("P2_PORT", ""))
    parser.add_argument(
        "--baud", type=int, default=int(os.getenv("P2_CONSOLE_BAUD", "230400"))
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--artifact-dir", type=pathlib.Path)
    parser.add_argument(
        "--lock-file",
        default=os.getenv("P2_LOCK_FILE") or str(monitor.DEFAULT_LOCK_FILE),
    )
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    parser.add_argument("--quiet", action="store_true")
    return parser


def _offline_parameters(args, text: str) -> Tuple[str, int, int]:
    inferred_sequence, inferred_bytes, inferred_passes = protocol.transcript_parameters(
        text
    )
    sequence = (
        protocol.normalize_sequence(args.sequence)
        if args.sequence is not None
        else inferred_sequence
    )
    byte_count = args.byte_count if args.byte_count is not None else inferred_bytes
    passes = args.passes if args.passes is not None else inferred_passes
    return sequence, byte_count, passes


def main(
    argv: Optional[Sequence[str]] = None,
    serial_factory=None,
    serial_exceptions: Optional[Iterable[type]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    process_factory: Callable[[Sequence[str]], object] = hil.default_process_factory,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
        datetime.timezone.utc
    ),
    lock_factory: Callable[..., object] = monitor.BoardLock,
    owner_probe: Callable[[str], Tuple[int, ...]] = hil.default_owner_probe,
    port_validator: Callable[[str], bool] = hil.is_character_device,
) -> int:
    args = build_parser().parse_args(argv)
    environment = hil.local_environment(os.environ) if env is None else dict(env)

    if args.parse_log is not None:
        if args.execute or args.ram_load or args.build_artifact is not None:
            print(
                "--parse-log cannot be combined with live or RAM-load options",
                file=sys.stderr,
            )
            return EXIT_SAFETY
        try:
            source = args.parse_log.expanduser().resolve()
            text = source.read_text(encoding="utf-8", errors="replace")
            sequence, byte_count, passes = _offline_parameters(args, text)
            result = protocol.parse_benchmark(text, sequence, byte_count, passes)
        except (OSError, ValueError) as exc:
            print("BENCHMARK LOG ERROR: {}".format(exc), file=sys.stderr)
            return EXIT_SAFETY

        if args.artifact_dir is not None:
            artifact_dir = args.artifact_dir.expanduser().resolve()
            if artifact_dir.exists():
                print(
                    "artifact directory already exists: {}".format(artifact_dir),
                    file=sys.stderr,
                )
                return EXIT_SAFETY
            artifact_dir.mkdir(parents=True)
            raw_log = artifact_dir / "console.raw"
            shutil.copy2(str(source), str(raw_log))
            _write_artifact(
                artifact_dir,
                raw_log,
                result,
                {
                    "schema": "p2-sd-benchmark-evidence-v1",
                    "capture_mode": "OFFLINE_PARSE",
                    "source_log": str(source),
                    "target_image_loaded_by_this_tool": False,
                    "started_utc": utc_timestamp(),
                    "ended_utc": utc_timestamp(),
                    "host": platform.platform(),
                    "python": sys.version,
                },
            )
        evidence = evidence_result(result, False)
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return EXIT_IMAGE_UNVERIFIED if result["complete"] else EXIT_PROOF_FAILED

    try:
        sequence = protocol.normalize_sequence(
            args.sequence if args.sequence is not None else secrets.randbits(32)
        )
        byte_count = (
            args.byte_count if args.byte_count is not None else protocol.DEFAULT_BYTES
        )
        passes = args.passes if args.passes is not None else protocol.DEFAULT_PASSES
        protocol.validate_parameters(byte_count, passes)
    except ValueError as exc:
        print("BENCHMARK CONFIGURATION ERROR: {}".format(exc), file=sys.stderr)
        return EXIT_SAFETY

    if args.ram_load and (
        byte_count != protocol.DEFAULT_BYTES or passes != protocol.DEFAULT_PASSES
    ):
        print(
            "BOUND PROOF REQUIRES exactly {} bytes and {} passes".format(
                protocol.DEFAULT_BYTES, protocol.DEFAULT_PASSES
            ),
            file=sys.stderr,
        )
        return EXIT_SAFETY
    if not args.ram_load and args.build_artifact is not None:
        print("--build-artifact requires --ram-load", file=sys.stderr)
        return EXIT_SAFETY

    command = protocol.command_line(sequence, byte_count, passes)
    if not args.execute:
        print(
            "DRY-RUN: no serial open, reset, RAM load, flash, mount, format, "
            "or SD write was performed",
            file=sys.stderr,
        )
        print("DRY-RUN COMMAND: {}".format(command), file=sys.stderr)
        if args.ram_load:
            print(
                "pass --execute --ram-load with P2_HIL=1, "
                "P2_ALLOW_RESET=1, exact P2_PORT, and --build-artifact",
                file=sys.stderr,
            )
        else:
            print(
                "pass --execute with P2_HIL=1 and an absolute --port",
                file=sys.stderr,
            )
        return EXIT_SAFETY
    if environment.get("P2_HIL", "0") != "1":
        print("HIL REQUIRED: set P2_HIL=1 before --execute", file=sys.stderr)
        return EXIT_SAFETY
    if args.ram_load and environment.get("P2_ALLOW_RESET", "0") != "1":
        print(
            "RESET AUTHORIZATION REQUIRED: set P2_ALLOW_RESET=1 for --ram-load",
            file=sys.stderr,
        )
        return EXIT_SAFETY
    if args.ram_load:
        try:
            bound_config = bound_config_from_args(args, environment, port_validator)
            started_utc = utc_timestamp(utc_now())
            capture = run_bound_capture(
                bound_config,
                command,
                protocol.done_marker(sequence),
                process_factory=process_factory,
                monotonic=monotonic,
                utc_now=utc_now,
                lock_factory=lock_factory,
                owner_probe=owner_probe,
            )
        except monitor.LockBusyError as exc:
            print("LOCK BUSY: {}".format(exc), file=sys.stderr)
            return EXIT_LOCK_BUSY
        except (SafetyError, hil.SafetyError, monitor.ConfigurationError) as exc:
            print("SAFETY REFUSAL: {}".format(exc), file=sys.stderr)
            return EXIT_SAFETY
        except OSError as exc:
            print("I/O ERROR: {}".format(monitor.safe_error(exc)), file=sys.stderr)
            return EXIT_PROOF_FAILED

        result = protocol.parse_benchmark(capture.text, sequence, byte_count, passes)
        result = validate_bound_result(result, bound_config)
        if capture.failure is not None:
            result = dict(result)
            result["complete"] = False
            result["status"] = "FAIL"
            result["errors"] = list(result["errors"]) + [capture.failure]

        loader_ok = capture.intentionally_terminated or capture.loader_returncode in (
            None,
            0,
        )
        immutable = capture.failure is None and loader_ok
        if capture.failure is None and not loader_ok:
            result = dict(result)
            result["complete"] = False
            result["status"] = "FAIL"
            result["errors"] = list(result["errors"]) + [
                "loadp2 exited with code {} after DONE".format(
                    capture.loader_returncode
                )
            ]
        if immutable:
            try:
                verify_bound_inputs(bound_config)
            except SafetyError as exc:
                immutable = False
                result = dict(result)
                result["complete"] = False
                result["status"] = "FAIL"
                result["errors"] = list(result["errors"]) + [str(exc)]

        image_bound = (
            immutable
            and capture.prompt_seen
            and capture.command_sent
            and capture.done_seen
        )
        proof_complete = image_bound and result["complete"] is True
        raw_log = bound_config.artifact_dir / "console.raw"
        transcript = bound_config.artifact_dir / "loadp2-transcript.raw"
        load_command = build_load_command(bound_config)
        metadata = {
            "schema": "p2-sd-benchmark-evidence-v1",
            "capture_mode": "LIVE_RAM_LOADED_BOUND",
            "target_image_loaded_by_this_tool": image_bound,
            "target_image_binding": (
                "BOUND: one pinned loadp2 terminal session RAM-loaded the exact "
                "hashed ELF and sent the benchmark command after NSH"
                if image_bound
                else "FAILED: RAM-load session did not establish complete image binding"
            ),
            "port": bound_config.port,
            "loader_baud": bound_config.loader_baud,
            "baud": bound_config.console_baud,
            "timeout_seconds": bound_config.timeout,
            "benchmark_command": command,
            "loadp2_command_argv": list(load_command),
            "loadp2_command_shell": shlex.join(load_command),
            "reset_flag": bound_config.reset_flag,
            "ram_only": True,
            "flash_options_present": False,
            "one_loader_terminal_session": True,
            "nsh_prompt_seen": capture.prompt_seen,
            "benchmark_command_sent": capture.command_sent,
            "done_seen": capture.done_seen,
            "loader_returncode": capture.loader_returncode,
            "loader_intentionally_terminated": capture.intentionally_terminated,
            "capture_failure": capture.failure,
            "build_artifact": str(bound_config.build_artifact),
            "build_status": str(bound_config.build_status),
            "build_status_sha256": bound_config.build_status_sha256,
            "build_profile": BOUND_PROFILE,
            "build_board": BOUND_BOARD,
            "nuttx_commit": bound_config.nuttx_commit,
            "apps_commit": bound_config.apps_commit,
            "image": str(bound_config.image),
            "image_sha256": bound_config.image_sha256,
            "config": str(bound_config.generated_config),
            "config_sha256": bound_config.config_sha256,
            "loadp2": str(bound_config.loadp2),
            "loadp2_sha256": bound_config.loadp2_sha256,
            "toolchain_lock": str(bound_config.toolchain_lock),
            "toolchain_lock_sha256": bound_config.toolchain_lock_sha256,
            "preserved_bound_inputs": capture.preserved_inputs,
            "loadp2_transcript": (
                {
                    "bytes": transcript.stat().st_size,
                    "sha256": sha256_file(transcript),
                }
                if transcript.is_file()
                else None
            ),
            "started_utc": started_utc,
            "ended_utc": utc_timestamp(utc_now()),
            "host": platform.platform(),
            "python": sys.version,
        }
        _write_artifact(bound_config.artifact_dir, raw_log, result, metadata)

        if proof_complete:
            aggregate = result["aggregates"]
            print(
                "P2 SD PROOF PASS - IMAGE BOUND: "
                "min {:.3f} MB/s ({:.3f} MiB/s), median {:.3f} MB/s, "
                "max {:.3f} MB/s".format(
                    aggregate["min_mb_per_s"],
                    aggregate["min_mib_per_s"],
                    aggregate["median_mb_per_s"],
                    aggregate["max_mb_per_s"],
                )
            )
            print("evidence: {}".format(bound_config.artifact_dir))
            return EXIT_OK

        reason = protocol.first_error(result)
        print("P2 SD BENCHMARK FAIL: {}".format(reason), file=sys.stderr)
        print("evidence: {}".format(bound_config.artifact_dir), file=sys.stderr)
        return EXIT_SAFETY if capture.safety_failure else EXIT_PROOF_FAILED

    if not args.port or not pathlib.Path(args.port).is_absolute():
        print("an absolute serial --port is required", file=sys.stderr)
        return EXIT_SAFETY
    if args.timeout <= 0 or args.timeout > 3600:
        print("--timeout must be in (0, 3600]", file=sys.stderr)
        return EXIT_SAFETY
    if args.baud <= 0 or args.lock_timeout < 0:
        print(
            "baud must be positive and lock timeout cannot be negative", file=sys.stderr
        )
        return EXIT_SAFETY

    artifact_dir = (
        args.artifact_dir.expanduser().resolve()
        if args.artifact_dir is not None
        else DEFAULT_ARTIFACT_ROOT / artifact_stamp()
    )
    if artifact_dir.exists():
        print(
            "artifact directory already exists: {}".format(artifact_dir),
            file=sys.stderr,
        )
        return EXIT_SAFETY

    raw_log = artifact_dir / "console.raw"
    normalized_log = artifact_dir / "console.log"
    started_utc = utc_timestamp()
    monitor_argv = [
        "--execute",
        "--port",
        args.port,
        "--baud",
        str(args.baud),
        "--timeout",
        str(args.timeout),
        "--expect",
        protocol.done_marker(sequence),
        "--send",
        command,
        "--send-ending",
        "cr",
        "--send-delay",
        "0.25",
        "--max-resets",
        "0",
        "--raw-log",
        str(raw_log),
        "--normalized-log",
        str(normalized_log),
        "--lock-file",
        str(pathlib.Path(args.lock_file).expanduser().resolve()),
        "--lock-timeout",
        str(args.lock_timeout),
    ]
    if args.quiet:
        monitor_argv.append("--quiet")

    monitor_exit = monitor.main(
        monitor_argv,
        serial_factory=serial_factory,
        serial_exceptions=serial_exceptions,
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    text = (
        raw_log.read_text(encoding="utf-8", errors="replace")
        if raw_log.is_file()
        else ""
    )
    result = protocol.parse_benchmark(text, sequence, byte_count, passes)
    if monitor_exit != monitor.EXIT_OK:
        result = dict(result)
        result["complete"] = False
        result["status"] = "FAIL"
        result["errors"] = list(result["errors"]) + [
            "serial monitor exited with code {}".format(monitor_exit)
        ]
    ended_utc = utc_timestamp()
    metadata = {
        "schema": "p2-sd-benchmark-evidence-v1",
        "capture_mode": "LIVE_SERIAL_READ_ONLY",
        "target_image_loaded_by_this_tool": False,
        "target_image_binding": (
            "UNVERIFIED: this serial-only tool records local candidate inputs but "
            "does not reset or load the target"
        ),
        "port": args.port,
        "baud": args.baud,
        "timeout_seconds": args.timeout,
        "command": command,
        "monitor_exit_code": monitor_exit,
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "host": platform.platform(),
        "python": sys.version,
    }
    _write_artifact(artifact_dir, raw_log, result, metadata)

    if result["complete"] and monitor_exit == monitor.EXIT_OK:
        aggregate = result["aggregates"]
        print(
            "P2 SD MEASUREMENT PASS - IMAGE UNVERIFIED: "
            "min {:.3f} MB/s ({:.3f} MiB/s), "
            "median {:.3f} MB/s, max {:.3f} MB/s".format(
                aggregate["min_mb_per_s"],
                aggregate["min_mib_per_s"],
                aggregate["median_mb_per_s"],
                aggregate["max_mb_per_s"],
            )
        )
        print("evidence: {}".format(artifact_dir))
        return EXIT_IMAGE_UNVERIFIED

    reason = protocol.first_error(result)
    if monitor_exit != monitor.EXIT_OK:
        reason = "serial monitor exit {}; {}".format(monitor_exit, reason)
    print("P2 SD BENCHMARK FAIL: {}".format(reason), file=sys.stderr)
    print("evidence: {}".format(artifact_dir), file=sys.stderr)
    return EXIT_PROOF_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
