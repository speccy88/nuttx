#!/usr/bin/env python3
"""Build a durable index over top-level P2 HIL artifact bundles."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib
import sys
from typing import Any


SCHEMA = "p2-hil-artifact-index-v1"


class ArtifactError(RuntimeError):
    """Raised when a top-level artifact cannot be indexed safely."""


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_status(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"cannot read {path}: {exc}") from exc

    if not isinstance(value, dict):
        raise ArtifactError(f"{path} does not contain a JSON object")

    status = value.get("status")
    if not isinstance(status, str) or not status:
        raise ArtifactError(f"{path} has no non-empty status")
    return value


def artifact_kind(name: str, status: dict[str, Any]) -> str:
    protocol = status.get("protocol")
    if isinstance(protocol, str) and protocol:
        return protocol

    profile = status.get("profile")
    if isinstance(profile, str) and profile:
        return f"build-{profile}" if "-build-" in name else profile

    marker = "Z-"
    if marker in name:
        return name.split(marker, 1)[1]
    return name


def scan_artifacts(root: pathlib.Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise ArtifactError(f"artifact root is not a directory: {root}")

    runs = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        if not directory.is_dir() or directory.is_symlink():
            continue

        status_path = directory / "status.json"
        if not status_path.is_file() or status_path.is_symlink():
            continue

        status = load_status(status_path)
        cycles_passed = status.get("cycles_passed")
        cycles_requested = status.get("cycles_requested")
        cycles = None
        if isinstance(cycles_passed, int) and isinstance(cycles_requested, int):
            cycles = {"passed": cycles_passed, "requested": cycles_requested}

        run = {
            "name": directory.name,
            "kind": artifact_kind(directory.name, status),
            "status": status["status"],
            "started_utc": status.get("started_utc"),
            "ended_utc": status.get("ended_utc"),
            "cycles": cycles,
            "status_path": str(status_path.relative_to(root)),
            "status_sha256": sha256_file(status_path),
        }

        for field in ("nuttx_commit", "apps_commit", "image_sha256"):
            value = status.get(field)
            if isinstance(value, str) and value:
                run[field] = value

        runs.append(run)

    return runs


def build_index(root: pathlib.Path, generated_utc: str | None = None
                ) -> dict[str, Any]:
    runs = scan_artifacts(root)
    counts = collections.Counter(run["status"] for run in runs)
    latest_pass_by_kind: dict[str, str] = {}

    for run in runs:
        if run["status"] == "PASS":
            latest_pass_by_kind[run["kind"]] = run["name"]

    if generated_utc is None:
        generated_utc = (dt.datetime.now(dt.timezone.utc)
                         .isoformat(timespec="seconds")
                         .replace("+00:00", "Z"))

    return {
        "schema": SCHEMA,
        "generated_utc": generated_utc,
        "artifact_root": str(root),
        "run_count": len(runs),
        "status_counts": dict(sorted(counts.items())),
        "latest_pass_by_kind": dict(sorted(latest_pass_by_kind.items())),
        "runs": runs,
    }


def render_markdown(index: dict[str, Any]) -> str:
    lines = [
        "# Propeller 2 HIL artifact index",
        "",
        f"Generated: `{index['generated_utc']}`",
        "",
        f"Top-level bundles indexed: **{index['run_count']}**.",
        "",
        "| Run | Kind | Status | Cycles | Started UTC | Ended UTC | "
        "Status SHA-256 |",
        "|---|---|---:|---:|---|---|---|",
    ]

    for run in index["runs"]:
        cycles = run["cycles"]
        cycle_text = ""
        if cycles is not None:
            cycle_text = f"{cycles['passed']}/{cycles['requested']}"

        lines.append(
            "| `{name}` | `{kind}` | **{status}** | {cycles} | {started} | "
            "{ended} | `{digest}` |".format(
                name=run["name"],
                kind=run["kind"],
                status=run["status"],
                cycles=cycle_text,
                started=run["started_utc"] or "",
                ended=run["ended_utc"] or "",
                digest=run["status_sha256"],
            )
        )

    lines.extend([
        "",
        "This index records top-level status bundles, including retained "
        "failures and in-progress runs. A PASS row is not a substitute for "
        "the detailed evidence inside that bundle.",
        "",
    ])
    return "\n".join(lines)


def write_atomic(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path,
                        default=pathlib.Path("artifacts/hil"))
    parser.add_argument("--json", type=pathlib.Path,
                        default=pathlib.Path("artifacts/hil/index.json"))
    parser.add_argument("--markdown", type=pathlib.Path,
                        default=pathlib.Path("artifacts/hil/index.md"))
    args = parser.parse_args()

    try:
        index = build_index(args.root)
        write_atomic(args.json, json.dumps(index, indent=2,
                                           sort_keys=True) + "\n")
        write_atomic(args.markdown, render_markdown(index))
    except ArtifactError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"PASS: indexed {index['run_count']} top-level P2 HIL bundles")
    print(args.json)
    print(args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
