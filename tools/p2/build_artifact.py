#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create and validate reproducible P2 build-artifact manifests."""

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Dict, Optional

FORMAT = "p2-build-artifact-v1"
SUPPORTED_BOARDS = ("p2-ec32mb", "p2-ec")
PASS_REQUIRED_FILES = (
    "System.map",
    "apps-source-status.txt",
    "build-command.txt",
    "build.log",
    "config",
    "disassembly.txt",
    "elf.txt",
    "input-relocations.txt",
    "nuttx",
    "nuttx.bin",
    "nuttx.map",
    "nuttx-source-status.txt",
    "size.txt",
    "symbols.txt",
    "status.txt",
    "toolchain.lock",
    "verify-elf.txt",
)


class BuildArtifactError(ValueError):
    """A build artifact is incomplete, changed, dirty, or mismatched."""


@dataclass(frozen=True)
class BuildArtifact:
    path: pathlib.Path
    status_path: pathlib.Path
    status_sha256: str
    board: str
    profile: str
    nuttx_commit: str
    apps_commit: str
    nuttx_branch: str
    apps_branch: str
    board_clock_hz: int
    binary_sha256: str
    elf_sha256: str
    source_clean: bool
    started_utc: str
    ended_utc: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "path": str(self.path),
            "status_path": str(self.status_path),
            "status_sha256": self.status_sha256,
            "board": self.board,
            "profile": self.profile,
            "nuttx_commit": self.nuttx_commit,
            "apps_commit": self.apps_commit,
            "nuttx_branch": self.nuttx_branch,
            "apps_branch": self.apps_branch,
            "board_clock_hz": self.board_clock_hz,
            "binary_sha256": self.binary_sha256,
            "elf_sha256": self.elf_sha256,
            "source_clean": self.source_clean,
            "started_utc": self.started_utc,
            "ended_utc": self.ended_utc,
        }


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: pathlib.Path) -> Dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildArtifactError("cannot read {}: {}".format(path, exc)) from exc
    if not isinstance(value, dict):
        raise BuildArtifactError("{} must contain a JSON object".format(path))
    return value


def _files(root: pathlib.Path) -> Dict[str, Dict[str, object]]:
    result = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in ("status.json", "status.json.tmp"):
            continue
        relative = path.relative_to(root).as_posix()
        result[relative] = {"size": path.stat().st_size, "sha256": sha256(path)}
    return result


def _toolchain_source_commits(path: pathlib.Path) -> Dict[str, str]:
    """Read the source commits pinned by the copied bootstrap lock."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise BuildArtifactError(
            "cannot read embedded toolchain lock {}: {}".format(path, exc)
        ) from exc
    names = {
        "nuttx_commit": "nuttx_commit",
        "nuttx_apps_commit": "apps_commit",
    }
    found: Dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name not in names:
            continue
        target = names[name]
        if target in found:
            raise BuildArtifactError("embedded toolchain lock repeats {}".format(name))
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise BuildArtifactError(
                "embedded toolchain lock {} is malformed".format(name)
            )
        found[target] = value
    missing = [name for name in ("nuttx_commit", "apps_commit") if name not in found]
    if missing:
        raise BuildArtifactError(
            "embedded toolchain lock lacks source commit(s): {}".format(
                ", ".join(missing)
            )
        )
    return found


def validate_toolchain_source_commits(
    path: pathlib.Path, nuttx_commit: str, apps_commit: str
) -> None:
    """Require a bootstrap lock for the exact source revisions being built."""

    locked_commits = _toolchain_source_commits(path)
    expected = {
        "nuttx_commit": nuttx_commit,
        "apps_commit": apps_commit,
    }
    for key, value in expected.items():
        if locked_commits[key] != value:
            raise BuildArtifactError(
                "toolchain lock {} {} does not match source {}".format(
                    key, locked_commits[key], value
                )
            )


def finalize_from_environment() -> None:
    env = os.environ
    root = pathlib.Path(env["P2_BUILD_ARTIFACT"]).resolve()
    status_text = env["P2_BUILD_STATUS"]
    exit_code = int(env["P2_BUILD_EXIT_CODE"])
    files = _files(root)
    config = root / "config"
    clock_hz = None
    if config.is_file():
        match = re.search(
            r"^CONFIG_P2_SYSCLK_HZ=(\d+)$",
            config.read_text(encoding="utf-8", errors="replace"),
            re.MULTILINE,
        )
        if match is not None:
            clock_hz = int(match.group(1))

    status = {
        "format": FORMAT,
        "status": status_text,
        "exit_code": exit_code,
        "board": env.get("P2_BUILD_BOARD", "p2-ec32mb"),
        "profile": env["P2_BUILD_PROFILE"],
        "started_utc": env["P2_BUILD_STARTED_UTC"],
        "ended_utc": env["P2_BUILD_ENDED_UTC"],
        "build_command": env["P2_BUILD_COMMAND"],
        "nuttx_branch": env["P2_BUILD_NUTTX_BRANCH"],
        "nuttx_commit": env["P2_BUILD_NUTTX_COMMIT"],
        "nuttx_commit_after": env["P2_BUILD_NUTTX_COMMIT_AFTER"],
        "apps_path": env["P2_BUILD_APPS_PATH"],
        "apps_branch": env["P2_BUILD_APPS_BRANCH"],
        "apps_commit": env["P2_BUILD_APPS_COMMIT"],
        "apps_commit_after": env["P2_BUILD_APPS_COMMIT_AFTER"],
        "nuttx_source_clean": env["P2_BUILD_NUTTX_CLEAN"] == "1",
        "apps_source_clean": env["P2_BUILD_APPS_CLEAN"] == "1",
        "source_clean": (
            env["P2_BUILD_NUTTX_CLEAN"] == "1" and env["P2_BUILD_APPS_CLEAN"] == "1"
        ),
        "p2llvm_root": env["P2_BUILD_P2LLVM_ROOT"],
        "compiler": env["P2_BUILD_COMPILER"],
        "jobs": int(env["P2_BUILD_JOBS"]),
        "board_clock_hz": clock_hz,
        "binary_sha256": files.get("nuttx.bin", {}).get("sha256"),
        "elf_sha256": files.get("nuttx", {}).get("sha256"),
        "files": files,
    }
    temporary = root / "status.json.tmp"
    temporary.write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(root / "status.json")


def load(
    path: pathlib.Path,
    image: Optional[pathlib.Path] = None,
    require_clean: bool = False,
) -> BuildArtifact:
    requested = pathlib.Path(path).expanduser().resolve()
    if requested.name == "status.json":
        root = requested.parent
        status_path = requested
    else:
        root = requested
        status_path = root / "status.json"
    if not root.is_dir():
        raise BuildArtifactError("build artifact directory is absent: {}".format(root))
    status = _read_json(status_path)
    for key, expected in (
        ("format", FORMAT),
        ("status", "PASS"),
        ("exit_code", 0),
    ):
        if status.get(key) != expected:
            raise BuildArtifactError(
                "build artifact {} must be {!r}, got {!r}".format(
                    key, expected, status.get(key)
                )
            )
    board = status.get("board")
    if board not in SUPPORTED_BOARDS:
        raise BuildArtifactError(
            "build artifact board must be one of {}, got {!r}".format(
                ", ".join(SUPPORTED_BOARDS), board
            )
        )
    if require_clean and (
        status.get("source_clean") is not True
        or status.get("nuttx_source_clean") is not True
        or status.get("apps_source_clean") is not True
    ):
        raise BuildArtifactError(
            "build artifact was not produced from clean source trees"
        )

    manifest = status.get("files")
    if not isinstance(manifest, dict):
        raise BuildArtifactError("build artifact has no file manifest")
    actual_names = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in ("status.json", "status.json.tmp")
    }
    if actual_names != set(manifest):
        raise BuildArtifactError("build artifact files do not match its manifest")
    for name, entry in manifest.items():
        relative = pathlib.PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise BuildArtifactError("build artifact manifest path is unsafe")
        path_value = root / name
        if not isinstance(entry, dict) or not path_value.is_file():
            raise BuildArtifactError("build artifact is missing {}".format(name))
        if entry.get("size") != path_value.stat().st_size:
            raise BuildArtifactError("build artifact {} size changed".format(name))
        if entry.get("sha256") != sha256(path_value):
            raise BuildArtifactError("build artifact {} SHA-256 changed".format(name))
    for name in PASS_REQUIRED_FILES:
        if name not in manifest:
            raise BuildArtifactError("build artifact is missing {}".format(name))
    if require_clean:
        for name in ("nuttx-source-status.txt", "apps-source-status.txt"):
            if (root / name).stat().st_size != 0:
                raise BuildArtifactError(
                    "build artifact {} proves the tree was dirty".format(name)
                )

    try:
        validate_toolchain_source_commits(
            root / "toolchain.lock",
            str(status.get("nuttx_commit")),
            str(status.get("apps_commit")),
        )
    except BuildArtifactError as exc:
        raise BuildArtifactError(
            str(exc).replace("toolchain lock", "embedded toolchain lock", 1)
        ) from exc

    profile = status.get("profile")
    clock_hz = status.get("board_clock_hz")
    for key in (
        "nuttx_commit",
        "nuttx_commit_after",
        "apps_commit",
        "apps_commit_after",
    ):
        if re.fullmatch(r"[0-9a-f]{40}", str(status.get(key))) is None:
            raise BuildArtifactError("build artifact {} is malformed".format(key))
    if require_clean and (
        status["nuttx_commit"] != status["nuttx_commit_after"]
        or status["apps_commit"] != status["apps_commit_after"]
    ):
        raise BuildArtifactError("source commit changed during the build")
    if not isinstance(profile, str) or not profile:
        raise BuildArtifactError("build artifact profile is missing")
    if not isinstance(clock_hz, int) or clock_hz <= 0:
        raise BuildArtifactError("build artifact board clock is missing")

    binary_sha = sha256(root / "nuttx.bin")
    if image is not None and sha256(pathlib.Path(image)) != binary_sha:
        raise BuildArtifactError("flash input does not match build nuttx.bin")
    return BuildArtifact(
        path=root,
        status_path=status_path,
        status_sha256=sha256(status_path),
        board=str(board),
        profile=profile,
        nuttx_commit=str(status["nuttx_commit"]),
        apps_commit=str(status["apps_commit"]),
        nuttx_branch=str(status.get("nuttx_branch") or ""),
        apps_branch=str(status.get("apps_branch") or ""),
        board_clock_hz=clock_hz,
        binary_sha256=binary_sha,
        elf_sha256=sha256(root / "nuttx"),
        source_clean=status.get("source_clean") is True,
        started_utc=str(status.get("started_utc") or ""),
        ended_utc=str(status.get("ended_utc") or ""),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finalize-environment", action="store_true")
    parser.add_argument("--artifact", type=pathlib.Path)
    parser.add_argument("--image", type=pathlib.Path)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--verify-toolchain-lock", type=pathlib.Path)
    parser.add_argument("--nuttx-commit")
    parser.add_argument("--apps-commit")
    args = parser.parse_args(argv)
    try:
        if args.finalize_environment:
            finalize_from_environment()
            return 0
        if args.verify_toolchain_lock is not None:
            if args.nuttx_commit is None or args.apps_commit is None:
                parser.error(
                    "--nuttx-commit and --apps-commit are required with "
                    "--verify-toolchain-lock"
                )
            validate_toolchain_source_commits(
                args.verify_toolchain_lock,
                args.nuttx_commit,
                args.apps_commit,
            )
            print("toolchain_lock_sources=verified")
            return 0
        if args.artifact is None:
            parser.error("--artifact is required when validating")
        artifact = load(args.artifact, args.image, args.require_clean)
        print("build_artifact={}".format(artifact.path))
        print("build_status_sha256={}".format(artifact.status_sha256))
        print("build_board={}".format(artifact.board))
        print("build_profile={}".format(artifact.profile))
        print("build_nuttx_commit={}".format(artifact.nuttx_commit))
        print("build_apps_commit={}".format(artifact.apps_commit))
        print("build_clock_hz={}".format(artifact.board_clock_hz))
        print("build_binary_sha256={}".format(artifact.binary_sha256))
        print(
            "build_source_clean={}".format("true" if artifact.source_clean else "false")
        )
        return 0
    except (BuildArtifactError, OSError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
