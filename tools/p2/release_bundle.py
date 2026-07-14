#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create and verify the installable dual-board P2 Edge release bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from typing import Iterable


FORMAT = "p2-flat-up-dual-board-release-bundle-v1"
DEFAULT_PREFIX = "p2-edge-flat-up-v0.1.1"
PLATFORM = "macos-arm64"
LOADP2_VERSION = "0.078"
SD_WRITER_SHA256 = "b71f5d92e6b491c7b62fdc4b80baa63cea24d3975e98d6df4e3d2e8ae1b412e4"
SD_WRITER = "P2ES_sdcard.bin"
SD_WRITER_LIMIT = 0x8000
SD_PAYLOAD_LIMIT = 0x80000 - 0x8000 - 4
P2_ELF_MACHINE = 300
HUB_RAM = 0x7C000
FLASH_PAYLOAD_OFFSET = 0x90
FLASH_PAGE_SIZE = 0x100
FLASH_ERASE_SIZE = 0x1000
FLASH_LARGE_ERASE_SIZE = 0x10000
FLASH_LARGE_ERASE_THRESHOLD_PAGES = 64
FLASH_INPUT_FORMAT = "loadp2-single-flash-input-v1"
CHECKSUMS = "SHA256SUMS.txt"
RELEASE_MANIFEST = "release-manifest.json"
INSTALLER = "install-p2.sh"
VERIFIER = "verify-release.py"
DEFAULT_SD_BOOT_BOARD = "p2-ec32mb"
SHOWCASE_HIL_FORMAT = "p2-showcase-hil-v1"
SHOWCASE_PROFILE = "showcase"
BASE_PROFILE = "base"
RELEASE_PROFILES = (BASE_PROFILE, SHOWCASE_PROFILE)
REQUIRED_SHOWCASE_HIL_STAGES = (
    "ordered boot and showcase readiness",
    "p2help",
    "/dev/userleds and leds driver path",
    "shell Tab completion",
    "shell Up-arrow history",
    "Ctrl-C interrupt and prompt return",
    "p2smartpins gpio",
    "p2smartpins edge",
    "p2smartpins uart",
    "p2smartpins analog",
    "external PWM Ctrl-C and prompt return",
    "/dev/pwm0 RC-safe open/start/stop smoke",
    "p2smartpins spi",
    "p2i2c BMP180 on P24/P25",
    "p2storage probe (read-only)",
)
BOARD_SHOWCASE_HIL_STAGES = {
    "p2-ec32mb": ("optional p2psram volatile write/read proof",),
    "p2-ec": ("Rev D no-PSRAM runtime contract",),
}
SHOWCASE_HIL_LOGS = {
    "raw_serial_sha256": "console.raw",
    "normalized_serial_sha256": "console.normalized.log",
    "command_transcript_sha256": "commands.jsonl",
}
BOARD_SPECS = {
    "p2-ec32mb": {
        "slug": "p2-ec32mb-revb",
        "config_board": "p2-ec32mb",
    },
    "p2-ec": {
        "slug": "p2-ec-revd",
        "config_board": "p2-ec",
    },
}


class ReleaseBundleError(ValueError):
    """A release input or packaged file is unsafe or inconsistent."""


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str, label: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None:
        raise ReleaseBundleError("{} is not a safe file name: {}".format(label, value))
    return value


def _release_names(prefix: str) -> dict[str, object]:
    _safe_name(prefix, "release prefix")
    boards = {}
    for board, spec in BOARD_SPECS.items():
        stem = prefix + "-" + spec["slug"]
        boards[board] = {
            "slug": spec["slug"],
            "ram_elf": stem + "-ram.elf",
            "flash_image": stem + "-flash.bin",
            "flash_manifest": stem + "-flash.bin.json",
            "sd_boot_image": stem + "-_BOOT_P2.BIX",
            "sd_boot_archive_path": ("boards/" + spec["slug"] + "/_BOOT_P2.BIX"),
            "config": stem + ".config",
        }
    return {
        "boards": boards,
        "default_sd_boot_image": "_BOOT_P2.BIX",
        "sd_writer": SD_WRITER,
        "loadp2": "loadp2-0.078-macos-arm64",
        "loadp2_license": "loadp2-LICENSE.txt",
        "evidence": prefix + "-evidence.tar.gz",
        "bundle": prefix + "-bundle-macos-arm64.tar.gz",
    }


def _read_json(path: pathlib.Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseBundleError("cannot read {}: {}".format(path, exc)) from exc
    if not isinstance(value, dict):
        raise ReleaseBundleError("{} must contain a JSON object".format(path))
    return value


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _erase_end(program_pages: int) -> int:
    remaining = program_pages
    erased = 0
    while remaining:
        block_size = (
            FLASH_LARGE_ERASE_SIZE
            if remaining > FLASH_LARGE_ERASE_THRESHOLD_PAGES
            else FLASH_ERASE_SIZE
        )
        block_pages = block_size // FLASH_PAGE_SIZE
        remaining -= min(remaining, block_pages)
        erased += block_size
    return erased


def flash_manifest(data: bytes) -> dict[str, object]:
    if not data:
        raise ReleaseBundleError("flash image is empty")
    if data.startswith(b"\x7fELF"):
        raise ReleaseBundleError("flash image must be raw binary, not ELF")
    if len(data) > HUB_RAM:
        raise ReleaseBundleError("flash image exceeds P2 Hub RAM")
    if len(data) % 4:
        raise ReleaseBundleError("flash image size is not four-byte aligned")
    payload_end = FLASH_PAYLOAD_OFFSET + len(data)
    pages = max(4, _align_up(payload_end, FLASH_PAGE_SIZE) // FLASH_PAGE_SIZE)
    return {
        "format": FLASH_INPUT_FORMAT,
        "image_size": len(data),
        "image_sha256": hashlib.sha256(data).hexdigest(),
        "payload_offset": FLASH_PAYLOAD_OFFSET,
        "payload_end": payload_end,
        "program_end": pages * FLASH_PAGE_SIZE,
        "erase_end": _erase_end(pages),
    }


def _verify_p2_elf(path: pathlib.Path) -> None:
    data = path.read_bytes()[:20]
    if len(data) < 20 or data[:7] != b"\x7fELF\x01\x01\x01":
        raise ReleaseBundleError("RAM image is not 32-bit little-endian ELF")
    if struct.unpack("<H", data[18:20])[0] != P2_ELF_MACHINE:
        raise ReleaseBundleError("RAM ELF machine is not Propeller 2")


def _verify_loadp2(path: pathlib.Path, require_executable: bool = True) -> None:
    data = path.read_bytes()
    if len(data) < 8:
        raise ReleaseBundleError("loadp2 is empty or truncated")
    magic, cpu_type = struct.unpack("<II", data[:8])
    if magic != 0xFEEDFACF or cpu_type != 0x0100000C:
        raise ReleaseBundleError("loadp2 is not thin macOS arm64 Mach-O")
    if b"version 0.078" not in data:
        raise ReleaseBundleError("loadp2 does not identify version 0.078")
    if b"program application to SPI flash" not in data:
        raise ReleaseBundleError("loadp2 does not contain -FLASH support")
    if b"In -CHIP mode" not in data or b"@ADDR=file" not in data:
        raise ReleaseBundleError(
            "loadp2 does not contain -CHIP explicit-address loading support"
        )
    if require_executable and not os.access(path, os.X_OK):
        raise ReleaseBundleError("loadp2 is not executable")


def _tar_info(path: pathlib.Path, arcname: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(arcname)
    stat = path.stat()
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    if path.is_dir():
        info.type = tarfile.DIRTYPE
        info.mode = stat.st_mode & 0o777
        info.size = 0
    elif path.is_file():
        info.type = tarfile.REGTYPE
        info.mode = stat.st_mode & 0o777
        info.size = stat.st_size
    else:
        raise ReleaseBundleError("tar input is not a regular file: {}".format(path))
    return info


def _write_tar_gz(
    output: pathlib.Path, members: Iterable[tuple[pathlib.Path, str]]
) -> None:
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for source, arcname in members:
                    if source.is_symlink():
                        raise ReleaseBundleError(
                            "release archive refuses symlink: {}".format(source)
                        )
                    info = _tar_info(source, arcname)
                    if source.is_file():
                        with source.open("rb") as stream:
                            archive.addfile(info, stream)
                    else:
                        archive.addfile(info)


def _tree_members(
    source: pathlib.Path, archive_root: str
) -> list[tuple[pathlib.Path, str]]:
    source = source.resolve()
    if not source.exists():
        raise ReleaseBundleError("evidence is absent: {}".format(source))
    members = [(source, archive_root)]
    if source.is_dir():
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source).as_posix()
            members.append((path, archive_root + "/" + relative))
    return members


def _write_checksums(root: pathlib.Path) -> None:
    paths = sorted(
        path for path in root.rglob("*") if path.is_file() and path != root / CHECKSUMS
    )
    text = "".join(
        "{}  {}\n".format(sha256(path), path.relative_to(root).as_posix())
        for path in paths
    )
    (root / CHECKSUMS).write_text(text, encoding="utf-8")


def _manifest_entry(root: pathlib.Path, name: str, role: str) -> dict[str, object]:
    path = root / name
    return {
        "role": role,
        "size": path.stat().st_size,
        "sha256": sha256(path),
    }


def _showcase_hil_status_error(
    status: object, expected: dict[str, str], board: str
) -> str | None:
    """Return why a status is not exact, release-bound showcase evidence."""

    if not isinstance(status, dict):
        return "status.json is not an object"
    for key, wanted in (
        ("format", SHOWCASE_HIL_FORMAT),
        ("status", "PASS"),
        ("exit_code", 0),
        ("board", board),
        ("profile", SHOWCASE_PROFILE),
        ("smp_enabled", False),
        ("single_serial_owner", True),
        ("serial_processes_started", 1),
        ("intentionally_terminated", True),
        ("storage_actions", ["probe"]),
        ("destructive_storage_actions", []),
    ):
        if status.get(key) != wanted:
            return "showcase HIL {} must be {!r}".format(key, wanted)

    gates = status.get("gates")
    if not isinstance(gates, dict) or any(
        gates.get(name) is not True
        for name in (
            "P2_HIL",
            "P2_ALLOW_RESET",
            "P2_ALLOW_LOOPBACK_TESTS",
        )
    ):
        return "showcase HIL safety gates are incomplete"
    if gates.get("P2_ALLOW_PSRAM_WRITE") is not (board == "p2-ec32mb"):
        return "showcase HIL safety gates are incomplete"

    build = status.get("build")
    if not isinstance(build, dict):
        return "showcase HIL build binding is missing"
    for key, wanted in (
        ("board", board),
        ("profile", SHOWCASE_PROFILE),
        ("source_clean", True),
        ("status_sha256", expected["build_status_sha256"]),
        ("elf_sha256", expected["elf_sha256"]),
        ("binary_sha256", expected["raw_binary_sha256"]),
        ("raw_binary_sha256", expected["raw_binary_sha256"]),
        ("nuttx_commit", expected["nuttx_commit"]),
        ("apps_commit", expected["apps_commit"]),
    ):
        if build.get(key) != wanted:
            return "showcase HIL build {} does not match release".format(key)

    stages = status.get("stages")
    if not isinstance(stages, list):
        return "showcase HIL stages are missing"
    observed = {}
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("name"), str):
            return "showcase HIL stage entry is malformed"
        name = stage["name"]
        if name in observed:
            return "duplicate showcase HIL stage: {}".format(name)
        observed[name] = stage.get("status")
        if stage.get("status") != "PASS":
            return "showcase HIL stage did not PASS: {}".format(name)
    required_stages = REQUIRED_SHOWCASE_HIL_STAGES + BOARD_SHOWCASE_HIL_STAGES[board]
    missing = [name for name in required_stages if observed.get(name) != "PASS"]
    if missing:
        return "required showcase HIL stage is missing: {}".format(missing[0])

    raw_bytes = status.get("raw_serial_bytes")
    if not isinstance(raw_bytes, int) or raw_bytes <= 0:
        return "showcase HIL raw serial byte count is missing"
    for field in SHOWCASE_HIL_LOGS:
        value = status.get(field)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            return "showcase HIL {} is malformed".format(field)
    return None


def _validate_showcase_hil_files(
    status_path: pathlib.Path, status: dict[str, object]
) -> str | None:
    for field, name in SHOWCASE_HIL_LOGS.items():
        path = status_path.parent / name
        if not path.is_file():
            return "showcase HIL evidence is missing {}".format(name)
        if sha256(path) != status[field]:
            return "showcase HIL {} SHA-256 mismatch".format(name)
    if (status_path.parent / "console.raw").stat().st_size != status[
        "raw_serial_bytes"
    ]:
        return "showcase HIL console.raw size mismatch"
    return None


def _validate_hardware_evidence(paths: list[pathlib.Path], build) -> None:
    expected = {
        "build_status_sha256": build.status_sha256,
        "elf_sha256": build.elf_sha256,
        "raw_binary_sha256": build.binary_sha256,
        "nuttx_commit": build.nuttx_commit,
        "apps_commit": build.apps_commit,
    }
    matched = []
    rejected = []
    for supplied in paths:
        root = supplied.expanduser().resolve()
        candidates = [root / "status.json"] if root.is_dir() else []
        if root.is_dir():
            candidates.extend(sorted(root.rglob("status.json")))
        for status_path in dict.fromkeys(candidates):
            if not status_path.is_file():
                continue
            status = _read_json(status_path)
            error = _showcase_hil_status_error(status, expected, build.board)
            if error is None:
                error = _validate_showcase_hil_files(status_path, status)
            if error is None:
                matched.append(status_path)
            else:
                rejected.append("{}: {}".format(status_path, error))
    if not matched:
        detail = "; ".join(rejected[:3])
        raise ReleaseBundleError(
            "HIL-VERIFIED {} evidence must contain exact {} evidence "
            "with all required PASS stages and release-bound logs{}".format(
                build.board,
                SHOWCASE_HIL_FORMAT,
                ": " + detail if detail else "",
            )
        )


def package(args: argparse.Namespace) -> pathlib.Path:
    output = args.output.expanduser().resolve()
    if output.exists():
        raise ReleaseBundleError("output already exists: {}".format(output))
    output.parent.mkdir(parents=True, exist_ok=True)

    tool_dir = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(tool_dir))
    import build_artifact  # pylint: disable=import-outside-toplevel

    artifact_inputs = {
        "p2-ec32mb": args.ec32mb_build_artifact,
        "p2-ec": args.ec_revd_build_artifact,
    }
    builds = {}
    try:
        for expected_board, artifact_path in artifact_inputs.items():
            build = build_artifact.load(artifact_path, require_clean=True)
            if build.board != expected_board:
                raise ReleaseBundleError(
                    "{} artifact identifies board {}, expected {}".format(
                        expected_board, build.board, expected_board
                    )
                )
            _safe_name(build.profile, "build profile")
            builds[expected_board] = build
    except build_artifact.BuildArtifactError as exc:
        raise ReleaseBundleError(str(exc)) from exc
    profiles = {build.profile for build in builds.values()}
    if len(profiles) != 1 or not profiles.issubset(RELEASE_PROFILES):
        raise ReleaseBundleError(
            "dual-board release builds must use the same supported profile "
            "(base or showcase)"
        )
    release_profile = next(iter(profiles))
    for attribute, label in (
        ("nuttx_commit", "NuttX commit"),
        ("apps_commit", "apps commit"),
    ):
        values = {getattr(build, attribute) for build in builds.values()}
        if len(values) != 1:
            raise ReleaseBundleError(
                "dual-board release build {} values do not match".format(label)
            )
    hardware_statuses = {
        "p2-ec32mb": args.ec32mb_hardware_status,
        "p2-ec": args.ec_revd_hardware_status,
    }
    if (
        release_profile == BASE_PROFILE
        and "HIL-VERIFIED" in hardware_statuses.values()
    ):
        raise ReleaseBundleError(
            "base profile releases must use HIL-REQUIRED hardware status"
        )
    if args.ec32mb_hardware_status == "HIL-VERIFIED" and not args.ec32mb_evidence:
        raise ReleaseBundleError(
            "HIL-VERIFIED EC32MB status requires hardware evidence"
        )
    if args.ec32mb_hardware_status == "HIL-VERIFIED":
        _validate_hardware_evidence(args.ec32mb_evidence, builds["p2-ec32mb"])
    if args.ec_revd_hardware_status == "HIL-VERIFIED" and not args.ec_revd_evidence:
        raise ReleaseBundleError(
            "HIL-VERIFIED P2-EC Rev D status requires hardware evidence"
        )
    if args.ec_revd_hardware_status == "HIL-VERIFIED":
        _validate_hardware_evidence(args.ec_revd_evidence, builds["p2-ec"])

    names = _release_names(args.prefix)
    board_names = names["boards"]
    assert isinstance(board_names, dict)
    loader = args.loadp2.expanduser().resolve()
    license_path = args.loadp2_license.expanduser().resolve()
    sd_writer = args.sd_writer.expanduser().resolve()
    _verify_loadp2(loader)
    if not license_path.is_file() or license_path.stat().st_size == 0:
        raise ReleaseBundleError("loadp2 license is absent or empty")
    license_data = license_path.read_bytes()
    if b"MIT License" not in license_data or b"SDCARD writer" not in license_data:
        raise ReleaseBundleError(
            "loadp2 license does not cover the bundled SDCARD writer"
        )
    if re.fullmatch(r"[0-9a-f]{64}", args.sd_writer_sha256) is None:
        raise ReleaseBundleError("SD writer SHA-256 is malformed")
    if not sd_writer.is_file() or sd_writer.stat().st_size == 0:
        raise ReleaseBundleError("P2ES_sdcard.bin is absent or empty")
    if sd_writer.stat().st_size > SD_WRITER_LIMIT:
        raise ReleaseBundleError("P2ES_sdcard.bin does not fit below Hub 0x8000")
    sd_writer_actual_sha256 = sha256(sd_writer)
    if sd_writer_actual_sha256 != args.sd_writer_sha256:
        raise ReleaseBundleError("P2ES_sdcard.bin SHA-256 mismatch")

    output.mkdir()
    try:
        copies = [
            (loader, names["loadp2"], 0o755),
            (license_path, names["loadp2_license"], 0o644),
            (sd_writer, names["sd_writer"], 0o644),
            (tool_dir / "install-release.sh", INSTALLER, 0o755),
            (pathlib.Path(__file__).resolve(), VERIFIER, 0o755),
        ]
        for board, build in builds.items():
            board_files = board_names[board]
            copies.extend(
                (
                    (build.path / "nuttx", board_files["ram_elf"], 0o644),
                    (build.path / "nuttx.bin", board_files["flash_image"], 0o644),
                    (build.path / "nuttx.bin", board_files["sd_boot_image"], 0o644),
                    (build.path / "config", board_files["config"], 0o644),
                )
            )
        copies.append(
            (
                builds[DEFAULT_SD_BOOT_BOARD].path / "nuttx.bin",
                names["default_sd_boot_image"],
                0o644,
            )
        )
        for source, destination, mode in copies:
            shutil.copyfile(source, output / destination)
            (output / destination).chmod(mode)

        for board, board_files in board_names.items():
            raw = (output / board_files["flash_image"]).read_bytes()
            if len(raw) > SD_PAYLOAD_LIMIT:
                raise ReleaseBundleError(
                    "{} image exceeds the in-situ SD writer limit".format(board)
                )
            (output / board_files["flash_manifest"]).write_text(
                json.dumps(flash_manifest(raw), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        evidence_members = []
        used_roots = set()
        evidence_inputs = {
            "p2-ec32mb": args.ec32mb_evidence,
            "p2-ec": args.ec_revd_evidence,
        }
        for board, build in builds.items():
            slug = BOARD_SPECS[board]["slug"]
            build_root = "evidence/{}/build".format(slug)
            used_roots.add(build_root)
            evidence_members.extend(_tree_members(build.path, build_root))
            for index, evidence in enumerate(evidence_inputs[board], start=1):
                evidence = evidence.expanduser().resolve()
                label = re.sub(r"[^A-Za-z0-9._-]", "-", evidence.name)
                label = label.strip(".-") or "evidence-{}".format(index)
                root_name = "evidence/{}/additional/{}".format(slug, label)
                if root_name in used_roots:
                    root_name += "-{}".format(index)
                used_roots.add(root_name)
                evidence_members.extend(_tree_members(evidence, root_name))
        _write_tar_gz(output / names["evidence"], evidence_members)

        roles = {
            names["default_sd_boot_image"]: "default-sd-boot-image",
            names["sd_writer"]: "p2-sd-writer",
            names["loadp2"]: "loadp2-macos-arm64",
            names["loadp2_license"]: "flexprop-loader-and-sd-writer-license",
            names["evidence"]: "evidence-archive",
            INSTALLER: "installer",
            VERIFIER: "verifier",
        }
        for board, board_files in board_names.items():
            slug = BOARD_SPECS[board]["slug"]
            roles.update(
                {
                    board_files["ram_elf"]: slug + "-ram-elf",
                    board_files["flash_image"]: slug + "-flash-image",
                    board_files["flash_manifest"]: slug + "-flash-manifest",
                    board_files["sd_boot_image"]: slug + "-sd-boot-image",
                    board_files["config"]: slug + "-config",
                }
            )
        files = {
            name: _manifest_entry(output, name, role)
            for name, role in sorted(roles.items())
        }
        manifest = {
            "format": FORMAT,
            "release_prefix": args.prefix,
            "architecture": "p2",
            "build_mode": "flat-up",
            "host_platform": PLATFORM,
            "loadp2_version": LOADP2_VERSION,
            "sd_writer_sha256": sd_writer_actual_sha256,
            "sd_writer_reference_sha256": SD_WRITER_SHA256,
            "sd_writer_matches_reference": (
                sd_writer_actual_sha256 == SD_WRITER_SHA256
            ),
            "license_covers": [names["loadp2"], names["sd_writer"]],
            "license_sha256": sha256(license_path),
            "default_sd_boot_board": DEFAULT_SD_BOOT_BOARD,
            "default_sd_boot_image": names["default_sd_boot_image"],
            "files": files,
            "install": {
                "loadp2": names["loadp2"],
                "loadp2_license": names["loadp2_license"],
                "sd_writer": names["sd_writer"],
                "evidence": names["evidence"],
                "bundle": names["bundle"],
            },
            "boards": {},
        }
        for board, build in builds.items():
            hardware_status = (
                args.ec32mb_hardware_status
                if board == "p2-ec32mb"
                else args.ec_revd_hardware_status
            )
            manifest["boards"][board] = {
                "slug": BOARD_SPECS[board]["slug"],
                "profile": build.profile,
                "build_status": "COMPILED",
                "hardware_status": hardware_status,
                "hardware_evidence_included": bool(evidence_inputs[board]),
                "nuttx_commit": build.nuttx_commit,
                "apps_commit": build.apps_commit,
                "build_status_sha256": build.status_sha256,
                "elf_sha256": build.elf_sha256,
                "raw_binary_sha256": build.binary_sha256,
                "board_clock_hz": build.board_clock_hz,
                "build_started_utc": build.started_utc,
                "build_ended_utc": build.ended_utc,
                "source_clean": build.source_clean,
                "install": board_names[board],
            }
        (output / RELEASE_MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_checksums(output)

        with tempfile.TemporaryDirectory() as directory:
            stage = pathlib.Path(directory) / args.prefix
            shutil.copytree(output, stage)
            for board, board_files in board_names.items():
                archive_boot = stage / board_files["sd_boot_archive_path"]
                archive_boot.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(output / board_files["sd_boot_image"], archive_boot)
                archive_boot.chmod(0o644)
            _write_checksums(stage)
            bundle_members = _tree_members(stage, args.prefix)
            _write_tar_gz(output / names["bundle"], bundle_members)
        _write_checksums(output)
        verify(output)
        return output
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def _checksum_map(root: pathlib.Path) -> dict[str, str]:
    path = root / CHECKSUMS
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReleaseBundleError("cannot read {}: {}".format(path, exc)) from exc
    checksums = {}
    for number, line in enumerate(lines, start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._/-]+)", line)
        if match is None:
            raise ReleaseBundleError("malformed checksum line {}".format(number))
        digest, name = match.groups()
        pure = pathlib.PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts:
            raise ReleaseBundleError("unsafe checksum path: {}".format(name))
        if name == CHECKSUMS or name in checksums:
            raise ReleaseBundleError("invalid duplicate checksum: {}".format(name))
        checksums[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != root / CHECKSUMS
    }
    if set(checksums) != actual:
        raise ReleaseBundleError("SHA256SUMS file list does not match bundle")
    for name, expected in checksums.items():
        if sha256(root / name) != expected:
            raise ReleaseBundleError("SHA-256 mismatch: {}".format(name))
    return checksums


def _verify_tar(path: pathlib.Path, expected_root: str) -> set[str]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseBundleError("invalid archive {}: {}".format(path, exc)) from exc
    if not members:
        raise ReleaseBundleError("archive is empty: {}".format(path))
    names = set()
    for member in members:
        pure = pathlib.PurePosixPath(member.name)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or not pure.parts
            or pure.parts[0] != expected_root
        ):
            raise ReleaseBundleError("unsafe archive member: {}".format(member.name))
        if not (member.isfile() or member.isdir()):
            raise ReleaseBundleError(
                "archive contains non-file member: {}".format(member.name)
            )
        if member.name in names:
            raise ReleaseBundleError(
                "archive contains duplicate member: {}".format(member.name)
            )
        names.add(member.name)
    return names


def _verify_distribution_archive(
    path: pathlib.Path,
    prefix: str,
    root: pathlib.Path,
    board_names: dict[str, dict[str, str]],
) -> None:
    members = _verify_tar(path, prefix)
    required = {
        prefix + "/" + board_files["sd_boot_archive_path"]
        for board_files in board_names.values()
    }
    if not required <= members:
        raise ReleaseBundleError("distribution archive lacks a board _BOOT_P2.BIX")
    try:
        with tarfile.open(path, "r:gz") as archive:
            for board, board_files in board_names.items():
                member_name = prefix + "/" + board_files["sd_boot_archive_path"]
                stream = archive.extractfile(member_name)
                if stream is None:
                    raise ReleaseBundleError(
                        "distribution archive SD image is unreadable"
                    )
                expected = (root / board_files["sd_boot_image"]).read_bytes()
                if stream.read() != expected:
                    raise ReleaseBundleError(
                        "distribution archive {} SD image mismatch".format(board)
                    )
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseBundleError(
            "cannot inspect distribution archive: {}".format(exc)
        ) from exc


def _verify_archived_hardware_evidence(
    path: pathlib.Path, board: str, board_slug: str, expected: dict[str, str]
) -> None:
    prefix = "evidence/{}/additional/".format(board_slug)
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = {member.name: member for member in archive.getmembers()}
            rejected = []
            for member in members.values():
                if (
                    not member.isfile()
                    or not member.name.startswith(prefix)
                    or not member.name.endswith("/status.json")
                    or member.size > 1024 * 1024
                ):
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    continue
                try:
                    status = json.loads(stream.read().decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    continue
                error = _showcase_hil_status_error(status, expected, board)
                if error is not None:
                    rejected.append("{}: {}".format(member.name, error))
                    continue
                base = member.name.rsplit("/", 1)[0]
                for field, name in SHOWCASE_HIL_LOGS.items():
                    log_member = members.get(base + "/" + name)
                    if (
                        log_member is None
                        or not log_member.isfile()
                        or log_member.size > 64 * 1024 * 1024
                    ):
                        error = "archived showcase HIL evidence lacks {}".format(name)
                        break
                    log_stream = archive.extractfile(log_member)
                    if log_stream is None:
                        error = "archived showcase HIL {} is unreadable".format(name)
                        break
                    digest = hashlib.sha256(log_stream.read()).hexdigest()
                    if digest != status[field]:
                        error = "archived showcase HIL {} SHA mismatch".format(name)
                        break
                    if (
                        name == "console.raw"
                        and log_member.size != status["raw_serial_bytes"]
                    ):
                        error = "archived showcase HIL console.raw size mismatch"
                        break
                if error is None:
                    return
                rejected.append("{}: {}".format(member.name, error))
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseBundleError(
            "cannot inspect hardware evidence archive: {}".format(exc)
        ) from exc
    raise ReleaseBundleError(
        "HIL-VERIFIED metadata lacks archived exact showcase PASS evidence "
        "tied to the EC32MB release image{}".format(
            ": " + "; ".join(rejected[:3]) if rejected else ""
        )
    )


def _verify_archived_build_statuses(
    path: pathlib.Path, board_manifest: dict[str, object]
) -> None:
    """Bind each archived clean build status to release metadata and images."""

    try:
        with tarfile.open(path, "r:gz") as archive:
            for board, spec in BOARD_SPECS.items():
                details = board_manifest[board]
                if not isinstance(details, dict):
                    raise ReleaseBundleError("release board metadata is malformed")
                member_name = "evidence/{}/build/status.json".format(spec["slug"])
                try:
                    member = archive.getmember(member_name)
                except KeyError as exc:
                    raise ReleaseBundleError(
                        "evidence archive lacks {}".format(member_name)
                    ) from exc
                if not member.isfile() or member.size > 1024 * 1024:
                    raise ReleaseBundleError(
                        "archived build status is not a bounded regular file"
                    )
                stream = archive.extractfile(member)
                if stream is None:
                    raise ReleaseBundleError("archived build status is unreadable")
                data = stream.read()
                if hashlib.sha256(data).hexdigest() != details["build_status_sha256"]:
                    raise ReleaseBundleError(
                        "{} archived build status SHA mismatch".format(board)
                    )
                try:
                    status = json.loads(data.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise ReleaseBundleError(
                        "{} archived build status is malformed".format(board)
                    ) from exc
                for key, expected in (
                    ("format", "p2-build-artifact-v1"),
                    ("status", "PASS"),
                    ("exit_code", 0),
                    ("board", board),
                    ("profile", details["profile"]),
                    ("source_clean", True),
                    ("nuttx_commit", details["nuttx_commit"]),
                    ("apps_commit", details["apps_commit"]),
                    ("elf_sha256", details["elf_sha256"]),
                    ("binary_sha256", details["raw_binary_sha256"]),
                ):
                    if status.get(key) != expected:
                        raise ReleaseBundleError(
                            "{} archived build status {} mismatch".format(board, key)
                        )
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseBundleError(
            "cannot inspect archived build status: {}".format(exc)
        ) from exc


def verify(root_value: pathlib.Path) -> dict[str, str]:
    root = pathlib.Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise ReleaseBundleError("release bundle is absent: {}".format(root))
    checksums = _checksum_map(root)
    manifest = _read_json(root / RELEASE_MANIFEST)
    for key, expected in (
        ("format", FORMAT),
        ("architecture", "p2"),
        ("build_mode", "flat-up"),
        ("host_platform", PLATFORM),
        ("loadp2_version", LOADP2_VERSION),
        ("default_sd_boot_board", DEFAULT_SD_BOOT_BOARD),
        ("default_sd_boot_image", "_BOOT_P2.BIX"),
    ):
        if manifest.get(key) != expected:
            raise ReleaseBundleError("release manifest {} mismatch".format(key))
    prefix = str(manifest.get("release_prefix") or "")
    names = _release_names(prefix)
    board_names = names["boards"]
    assert isinstance(board_names, dict)
    install = manifest.get("install")
    expected_install = {
        "loadp2": names["loadp2"],
        "loadp2_license": names["loadp2_license"],
        "sd_writer": names["sd_writer"],
        "evidence": names["evidence"],
        "bundle": names["bundle"],
    }
    if install != expected_install:
        raise ReleaseBundleError("release install file mapping mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ReleaseBundleError("release manifest has no file map")
    expected_roles = {
        names["default_sd_boot_image"]: "default-sd-boot-image",
        names["sd_writer"]: "p2-sd-writer",
        names["loadp2"]: "loadp2-macos-arm64",
        names["loadp2_license"]: "flexprop-loader-and-sd-writer-license",
        names["evidence"]: "evidence-archive",
        INSTALLER: "installer",
        VERIFIER: "verifier",
    }
    for board, board_files in board_names.items():
        slug = BOARD_SPECS[board]["slug"]
        expected_roles.update(
            {
                board_files["ram_elf"]: slug + "-ram-elf",
                board_files["flash_image"]: slug + "-flash-image",
                board_files["flash_manifest"]: slug + "-flash-manifest",
                board_files["sd_boot_image"]: slug + "-sd-boot-image",
                board_files["config"]: slug + "-config",
            }
        )
    if set(files) != set(expected_roles):
        raise ReleaseBundleError("release manifest file set mismatch")
    for name, role in expected_roles.items():
        entry = files.get(name)
        path = root / name
        if not isinstance(entry, dict) or entry.get("role") != role:
            raise ReleaseBundleError("release role mismatch: {}".format(name))
        if entry.get("size") != path.stat().st_size:
            raise ReleaseBundleError("release size mismatch: {}".format(name))
        if entry.get("sha256") != sha256(path):
            raise ReleaseBundleError("release manifest SHA mismatch: {}".format(name))

    allowed = set(expected_roles) | {RELEASE_MANIFEST}
    archive_boots = {
        board_files["sd_boot_archive_path"] for board_files in board_names.values()
    }
    present_archive_boots = archive_boots & set(checksums)
    if present_archive_boots and present_archive_boots != archive_boots:
        raise ReleaseBundleError("release has an incomplete SD boot tree")
    allowed |= present_archive_boots
    if names["bundle"] in checksums:
        allowed.add(names["bundle"])
    if set(checksums) != allowed:
        raise ReleaseBundleError("release contains an unexpected file")

    loader = root / names["loadp2"]
    _verify_loadp2(loader)
    license_file = root / names["loadp2_license"]
    license_data = license_file.read_bytes()
    if not license_data:
        raise ReleaseBundleError("loadp2 license is empty")
    if b"MIT License" not in license_data or b"SDCARD writer" not in license_data:
        raise ReleaseBundleError("bundled license does not cover the SDCARD writer")
    if manifest.get("license_sha256") != sha256(license_file):
        raise ReleaseBundleError("bundled license SHA mismatch")
    if manifest.get("license_covers") != [names["loadp2"], names["sd_writer"]]:
        raise ReleaseBundleError("license scope must cover loadp2 and P2ES_sdcard.bin")
    writer = root / names["sd_writer"]
    writer_digest = sha256(writer)
    if writer.stat().st_size <= 0 or writer.stat().st_size > SD_WRITER_LIMIT:
        raise ReleaseBundleError("P2ES_sdcard.bin has an invalid size")
    recorded_writer = manifest.get("sd_writer_sha256")
    if (
        not isinstance(recorded_writer, str)
        or re.fullmatch(r"[0-9a-f]{64}", recorded_writer) is None
        or writer_digest != recorded_writer
    ):
        raise ReleaseBundleError("P2ES_sdcard.bin recorded SHA mismatch")
    if manifest.get("sd_writer_reference_sha256") != SD_WRITER_SHA256:
        raise ReleaseBundleError("SD writer reference SHA mismatch")
    if manifest.get("sd_writer_matches_reference") != (
        writer_digest == SD_WRITER_SHA256
    ):
        raise ReleaseBundleError("SD writer reference-match flag is wrong")

    board_manifest = manifest.get("boards")
    if not isinstance(board_manifest, dict) or set(board_manifest) != set(BOARD_SPECS):
        raise ReleaseBundleError("release board set mismatch")
    profiles = set()
    nuttx_commits = set()
    apps_commits = set()
    raw_images = {}
    elf_digests = {}
    for board, spec in BOARD_SPECS.items():
        details = board_manifest.get(board)
        board_files = board_names[board]
        if not isinstance(details, dict):
            raise ReleaseBundleError("release board metadata is malformed")
        for key, expected in (
            ("slug", spec["slug"]),
            ("build_status", "COMPILED"),
            ("source_clean", True),
            ("install", board_files),
        ):
            if details.get(key) != expected:
                raise ReleaseBundleError("{} metadata {} mismatch".format(board, key))
        profile = details.get("profile")
        if not isinstance(profile, str) or not profile:
            raise ReleaseBundleError("{} profile is missing".format(board))
        _safe_name(profile, "{} profile".format(board))
        profiles.add(profile)
        for key, values in (
            ("nuttx_commit", nuttx_commits),
            ("apps_commit", apps_commits),
        ):
            commit = details.get(key)
            if (
                not isinstance(commit, str)
                or re.fullmatch(r"[0-9a-f]{40}", commit) is None
            ):
                raise ReleaseBundleError(
                    "{} metadata {} is malformed".format(board, key)
                )
            values.add(commit)
        status_digest = details.get("build_status_sha256")
        if (
            not isinstance(status_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", status_digest) is None
        ):
            raise ReleaseBundleError(
                "{} build status SHA-256 is malformed".format(board)
            )
        hardware_status = details.get("hardware_status")
        if hardware_status not in ("HIL-REQUIRED", "HIL-VERIFIED"):
            raise ReleaseBundleError("invalid {} hardware status".format(board))
        if (
            hardware_status == "HIL-VERIFIED"
            and details.get("hardware_evidence_included") is not True
        ):
            raise ReleaseBundleError(
                "HIL-VERIFIED status lacks included hardware evidence"
            )

        ram_elf = root / board_files["ram_elf"]
        _verify_p2_elf(ram_elf)
        elf_digests[board] = sha256(ram_elf)
        raw = (root / board_files["flash_image"]).read_bytes()
        raw_images[board] = raw
        for key, observed in (
            ("elf_sha256", elf_digests[board]),
            ("raw_binary_sha256", hashlib.sha256(raw).hexdigest()),
        ):
            if details.get(key) != observed:
                raise ReleaseBundleError("{} metadata {} mismatch".format(board, key))
        if (root / board_files["sd_boot_image"]).read_bytes() != raw:
            raise ReleaseBundleError(
                "{} board-specific SD image differs from flash image".format(board)
            )
        observed_flash = _read_json(root / board_files["flash_manifest"])
        if observed_flash != flash_manifest(raw):
            raise ReleaseBundleError("{} flash image manifest mismatch".format(board))
        config = (root / board_files["config"]).read_text(
            encoding="utf-8", errors="strict"
        )
        config_lines = config.splitlines()
        for required in (
            'CONFIG_ARCH="p2"',
            'CONFIG_ARCH_BOARD="{}"'.format(spec["config_board"]),
            "CONFIG_BUILD_FLAT=y",
        ):
            if required not in config_lines:
                raise ReleaseBundleError(
                    "{} release config lacks {}".format(board, required)
                )
        if "CONFIG_SMP=y" in config_lines:
            raise ReleaseBundleError(
                "{} release config unexpectedly enables SMP".format(board)
            )
        if present_archive_boots:
            archive_boot = root / board_files["sd_boot_archive_path"]
            if archive_boot.read_bytes() != raw:
                raise ReleaseBundleError(
                    "{} archive _BOOT_P2.BIX differs from flash image".format(board)
                )
    if len(profiles) != 1 or not profiles.issubset(RELEASE_PROFILES):
        raise ReleaseBundleError(
            "dual-board release profiles must be the same supported profile "
            "(base or showcase)"
        )
    release_profile = next(iter(profiles))
    if release_profile == BASE_PROFILE and any(
        details["hardware_status"] == "HIL-VERIFIED"
        for details in board_manifest.values()
    ):
        raise ReleaseBundleError(
            "base profile releases must use HIL-REQUIRED hardware status"
        )
    if len(nuttx_commits) != 1:
        raise ReleaseBundleError("dual-board NuttX commits do not match")
    if len(apps_commits) != 1:
        raise ReleaseBundleError("dual-board apps commits do not match")
    if (root / names["default_sd_boot_image"]).read_bytes() != raw_images[
        DEFAULT_SD_BOOT_BOARD
    ]:
        raise ReleaseBundleError("default _BOOT_P2.BIX is not the EC32MB Rev B image")
    evidence_members = _verify_tar(root / names["evidence"], "evidence")
    required_evidence = {
        "evidence/{}/build/status.json".format(spec["slug"])
        for spec in BOARD_SPECS.values()
    }
    if not required_evidence <= evidence_members:
        raise ReleaseBundleError("evidence archive lacks a board build status")
    _verify_archived_build_statuses(root / names["evidence"], board_manifest)
    if board_manifest["p2-ec32mb"]["hardware_status"] == "HIL-VERIFIED":
        _verify_archived_hardware_evidence(
            root / names["evidence"],
            "p2-ec32mb",
            BOARD_SPECS["p2-ec32mb"]["slug"],
            {
                "build_status_sha256": board_manifest["p2-ec32mb"][
                    "build_status_sha256"
                ],
                "elf_sha256": elf_digests["p2-ec32mb"],
                "raw_binary_sha256": hashlib.sha256(
                    raw_images["p2-ec32mb"]
                ).hexdigest(),
                "nuttx_commit": board_manifest["p2-ec32mb"]["nuttx_commit"],
                "apps_commit": board_manifest["p2-ec32mb"]["apps_commit"],
            },
        )
    if board_manifest["p2-ec"]["hardware_status"] == "HIL-VERIFIED":
        _verify_archived_hardware_evidence(
            root / names["evidence"],
            "p2-ec",
            BOARD_SPECS["p2-ec"]["slug"],
            {
                "build_status_sha256": board_manifest["p2-ec"][
                    "build_status_sha256"
                ],
                "elf_sha256": elf_digests["p2-ec"],
                "raw_binary_sha256": hashlib.sha256(
                    raw_images["p2-ec"]
                ).hexdigest(),
                "nuttx_commit": board_manifest["p2-ec"]["nuttx_commit"],
                "apps_commit": board_manifest["p2-ec"]["apps_commit"],
            },
        )
    if names["bundle"] in checksums:
        _verify_distribution_archive(root / names["bundle"], prefix, root, board_names)
    return {
        "release_prefix": prefix,
        "default_sd_boot_board": DEFAULT_SD_BOOT_BOARD,
        "default_sd_boot_image": names["default_sd_boot_image"],
        "p2_ec32mb_ram_elf": board_names["p2-ec32mb"]["ram_elf"],
        "p2_ec32mb_flash_image": (board_names["p2-ec32mb"]["flash_image"]),
        "p2_ec32mb_sd_boot_image": (board_names["p2-ec32mb"]["sd_boot_image"]),
        "p2_ec_ram_elf": board_names["p2-ec"]["ram_elf"],
        "p2_ec_flash_image": board_names["p2-ec"]["flash_image"],
        "p2_ec_sd_boot_image": board_names["p2-ec"]["sd_boot_image"],
        "loadp2": names["loadp2"],
        "sd_writer": names["sd_writer"],
        "evidence": names["evidence"],
        "bundle": names["bundle"],
    }


def run_with_timeout(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise ReleaseBundleError("run requires a command")
    try:
        timeout = args.timeout if args.timeout > 0 else None
        return subprocess.run(command, timeout=timeout, check=False).returncode
    except subprocess.TimeoutExpired:
        print(
            "ERROR: command timed out after {} seconds".format(args.timeout),
            file=sys.stderr,
        )
        return 124
    except KeyboardInterrupt:
        return 130


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command_name", required=True)
    package_parser = subparsers.add_parser("package")
    package_parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    package_parser.add_argument(
        "--ec32mb-build-artifact", required=True, type=pathlib.Path
    )
    package_parser.add_argument(
        "--ec-revd-build-artifact", required=True, type=pathlib.Path
    )
    package_parser.add_argument("--loadp2", required=True, type=pathlib.Path)
    package_parser.add_argument("--loadp2-license", required=True, type=pathlib.Path)
    package_parser.add_argument("--sd-writer", required=True, type=pathlib.Path)
    package_parser.add_argument("--sd-writer-sha256", default=SD_WRITER_SHA256)
    package_parser.add_argument(
        "--ec32mb-evidence", action="append", default=[], type=pathlib.Path
    )
    package_parser.add_argument(
        "--ec-revd-evidence", action="append", default=[], type=pathlib.Path
    )
    package_parser.add_argument(
        "--ec32mb-hardware-status",
        choices=("HIL-REQUIRED", "HIL-VERIFIED"),
        default="HIL-REQUIRED",
    )
    package_parser.add_argument(
        "--ec-revd-hardware-status",
        choices=("HIL-REQUIRED", "HIL-VERIFIED"),
        default="HIL-REQUIRED",
    )
    package_parser.add_argument("--output", required=True, type=pathlib.Path)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("bundle", type=pathlib.Path)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--timeout", required=True, type=float)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command_name == "package":
            output = package(args)
            print("HOST-VERIFIED P2 release bundle: {}".format(output))
            return 0
        if args.command_name == "verify":
            result = verify(args.bundle)
            print("HOST-VERIFIED P2 release checksums and manifests")
            for key, value in result.items():
                print("{}={}".format(key, value))
            return 0
        return run_with_timeout(args)
    except (OSError, ReleaseBundleError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
