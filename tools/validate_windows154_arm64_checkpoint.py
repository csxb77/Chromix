#!/usr/bin/env python3
"""Validate the immutable run-37455471388 Windows ARM64 stage-12 checkpoint."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

if __package__:
    from .validate_windows_snapshot import Client
else:
    from validate_windows_snapshot import Client

REPOSITORY = "xiaozhou26/Chromix"
WORKFLOW = "build-win-arm64-github"
RUN_ID = 37455471388
JOB_ID = 113438156537
DONOR_SHA = "15a6425cfbd7a7ecd4a69ad206d0650778bf8253"
BRANCH = "main"
ATTEMPT = 1
STAGE = 12
VERSION = "154.0.8037.97"
PREFIX = "win-arm64-tree-s12-attempt-1-part"
ARTIFACTS = (
    {"id": 11582775420, "name": PREFIX + "1", "size_in_bytes": 9663676664,
     "expired": False, "digest": "sha256:eb4f963bf75a727b90f7c3bdb85d7ec8faf99112bcbe0b9e7187885bc2b629f4"},
    {"id": 11582590872, "name": PREFIX + "2", "size_in_bytes": 3270690206,
     "expired": False, "digest": "sha256:2e10f4f5398ad2bb04fcd8257225d4a783177ad95767eb1e5c42a0e24adfb0ae"},
)
MARKERS = (
    ".chromix-target-arch", "src/chrome/VERSION",
    "src/.chromix-upstream-restored.json", "src/.chromix-restored-patches.json",
    "src/.chromix-source-ready", "src/.chromix-source-unpacked", "src/out/Default/args.gn",
)
FORBIDDEN = {
    "src/.chromix-windows-snapshot-migration.json",
    "src/.chromix-windows154-arm64-migration.json",
    "src/.chromix-domain-substitution-in-progress",
    "src/.chromix-restored-patches-in-progress",
    "src/.chromix-layer-in-progress", "src/.chromix-patch-in-progress",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def exact(data: dict, expected: dict, label: str) -> None:
    require(isinstance(data, dict), label + " is not an object")
    require(all(type(data.get(key)) is type(value) and data[key] == value
                for key, value in expected.items()), label + " mismatch")


def objects(items, label: str) -> list[dict]:
    require(isinstance(items, list) and all(isinstance(item, dict)
            and isinstance(item.get("name"), str) for item in items), "invalid " + label)
    return items


def validate_metadata(client, repository: str = REPOSITORY) -> dict:
    require(repository == REPOSITORY, "checkpoint repository mismatch")
    run = client.get(f"/actions/runs/{RUN_ID}")
    exact(run, {"id": RUN_ID, "path": f".github/workflows/{WORKFLOW}.yml",
                "head_branch": BRANCH, "head_sha": DONOR_SHA, "run_attempt": ATTEMPT,
                "status": "completed", "conclusion": "failure"}, "checkpoint run identity")
    require(run.get("event") in ("push", "workflow_dispatch"), "checkpoint run event mismatch")
    for key in ("repository", "head_repository"):
        exact(run.get(key), {"full_name": REPOSITORY}, "checkpoint " + key)
    jobs = objects(client.items(f"/actions/runs/{RUN_ID}/attempts/{ATTEMPT}/jobs", "jobs"), "jobs")
    candidates = [job for job in jobs if job.get("id") == JOB_ID
                  or job["name"] == "stage 12 (resume compile)"]
    require(len(candidates) == 1, "exact checkpoint job is missing or ambiguous")
    job = candidates[0]
    exact(job, {"id": JOB_ID, "name": "stage 12 (resume compile)", "run_id": RUN_ID,
                "run_attempt": ATTEMPT, "head_sha": DONOR_SHA, "status": "completed",
                "conclusion": "failure"}, "checkpoint job identity")
    steps = objects(job.get("steps"), "checkpoint steps")
    for name in ("Ensure build tree snapshot", "Upload tree part 1", "Upload tree part 2"):
        matches = [step for step in steps if step["name"] == name]
        require(len(matches) == 1, "checkpoint step missing or ambiguous: " + name)
        exact(matches[0], {"status": "completed", "conclusion": "success"}, "checkpoint step " + name)
    listed = objects(client.items(f"/actions/runs/{RUN_ID}/artifacts", "artifacts"), "artifacts")
    selected = sorted([item for item in listed if item["name"].startswith(PREFIX)
                       or item.get("id") in {part["id"] for part in ARTIFACTS}], key=lambda item: item["name"])
    require(len(selected) == len(ARTIFACTS), "checkpoint artifact set missing or ambiguous")
    for item, expected in zip(selected, ARTIFACTS):
        exact(item, expected, "checkpoint artifact identity")
        exact(item.get("workflow_run"), {"id": RUN_ID, "head_sha": DONOR_SHA,
                                          "head_branch": BRANCH}, "checkpoint artifact origin")
    return {"repository": REPOSITORY, "platform": "windows", "arch": "arm64", "workflow": WORKFLOW,
            "run_id": RUN_ID, "job_id": JOB_ID, "attempt": ATTEMPT, "stage": STAGE,
            "head_branch": BRANCH, "head_sha": DONOR_SHA, "pattern": PREFIX + "*",
            "artifacts": [dict(item) for item in ARTIFACTS]}


def validate_snapshot_files(files: dict[str, bytes]) -> dict:
    require(set(MARKERS) <= files.keys(), "snapshot markers missing")
    try:
        text = {name: files[name].decode("utf-8-sig").strip() for name in MARKERS}
    except UnicodeDecodeError:
        raise ValueError("snapshot marker is not UTF-8") from None
    require(text[".chromix-target-arch"] == "arm64", "snapshot architecture mismatch")
    version = text["src/chrome/VERSION"]
    fields = re.findall(r"(?m)^(MAJOR|MINOR|BUILD|PATCH)=(\d+)\s*$", version)
    require(len(fields) == 4 and len(dict(fields)) == 4
            and ".".join(dict(fields).get(key, "") for key in ("MAJOR", "MINOR", "BUILD", "PATCH")) == VERSION,
            "snapshot Chromium VERSION mismatch")
    receipt = json.loads(text["src/.chromix-upstream-restored.json"])
    exact(receipt, {"status": "restored", "platform": "windows", "arch": "arm64"}, "upstream receipt")
    exact(receipt.get("identity"), {"arch": "arm64"}, "upstream receipt identity")
    patches = json.loads(text["src/.chromix-restored-patches.json"])
    exact(patches, {"schema_version": 1}, "patch receipt schema")
    require(isinstance(patches.get("identity_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", patches["identity_sha256"])
            and isinstance(patches.get("outputs"), dict) and bool(patches["outputs"]), "patch receipt is incomplete")
    identity = patches.get("identity")
    exact(identity, {"platform": "windows"}, "patch receipt identity")
    exact(identity.get("selection"), {"version": VERSION}, "patch receipt selection")
    series = identity.get("series")
    require(isinstance(series, dict) and isinstance(series.get("patches"), list)
            and bool(series["patches"]), "patch receipt series missing")
    require(text["src/.chromix-source-ready"].startswith(VERSION + "|")
            and text["src/.chromix-source-unpacked"] == VERSION, "snapshot ready/version markers mismatch")
    args = text["src/out/Default/args.gn"]
    for key, value in (("target_cpu", "arm64"), ("target_os", "win")):
        require(len(re.findall(rf"(?m)^\s*{key}\s*=", args)) == 1
                and re.search(rf'(?m)^\s*{key}\s*=\s*"{value}"\s*(?:#.*)?$', args),
                "snapshot GN architecture mismatch")
    return {"status": "verified", "platform": "windows", "arch": "arm64", "version": VERSION,
            "restored_patches": len(series["patches"]), "restored_outputs": len(patches["outputs"])}


def validate_archive_listing(listing: str) -> dict[str, dict]:
    members = {}
    for block in re.split(r"\r?\n\s*\r?\n", listing.strip()):
        record = {}
        for line in block.splitlines():
            if " = " in line:
                key, value = line.split(" = ", 1)
                require(key not in record, "ambiguous archive listing field")
                record[key] = value
        require("Path" in record, "invalid archive member listing")
        name = record["Path"].replace("\\", "/")
        parts = name.split("/")
        require(parts[0] == "chromix" and all(part not in ("", ".", "..")
                and not any(ord(char) < 32 or char in ':<>"|?*' for char in part)
                and not part.endswith((" ", "."))
                and not re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                for part in parts), "unsafe archive path")
        require(not any(key in record for key in ("Symbolic Link", "Hard Link", "Reparse"))
                and not re.search(r"(?:^|\s)l[rwx-]{9}(?:\s|$)", record.get("Attributes", "")),
                "archive links are forbidden")
        require(record.get("Anti", "-") == "-", "archive anti-items are forbidden")
        folded = name.casefold()
        require(folded not in members, "duplicate archive path")
        require(not folded.startswith("chromix/src/out/chromix")
                and folded not in {"chromix/" + name for name in FORBIDDEN}
                and not any(part.startswith(".chromix-upstream-restore-") for part in parts),
                "snapshot contains cold output or interrupted migration state")
        members[folded] = record
    for marker in MARKERS:
        record = members.get("chromix/" + marker.lower())
        require(record is not None and record.get("Folder", "-") == "-"
                and re.fullmatch(r"[0-9]+", record.get("Size", ""))
                and 0 < int(record["Size"]) <= 32 * 1024 * 1024, "snapshot marker missing or oversized: " + marker)
    return members


def verify_snapshot_archive(archive: Path, seven_zip: str | None = None) -> dict:
    command = seven_zip or shutil.which("7z.exe") or shutil.which("7z") or shutil.which("7zz")
    require(bool(command), "7z is required to inspect the checkpoint")
    listing = subprocess.run([command, "l", "-slt", "-ba", "-sccUTF-8", str(archive)],
                             check=True, capture_output=True).stdout.decode("utf-8", "strict")
    validate_archive_listing(listing)
    files = {}
    for marker in MARKERS:
        files[marker] = subprocess.run([command, "e", str(archive), "chromix/" + marker, "-so"],
                                       check=True, capture_output=True).stdout
    result = validate_snapshot_files(files)
    result["archive"] = str(archive)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--metadata-report", type=Path)
    modes.add_argument("--snapshot", type=Path)
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--seven-zip")
    args = parser.parse_args(argv)
    require(args.repository == REPOSITORY, "checkpoint repository mismatch")
    if args.metadata_report:
        result = validate_metadata(Client(REPOSITORY, os.environ.get("GH_TOKEN", "")), args.repository)
        report = args.metadata_report
    else:
        result = verify_snapshot_archive(args.snapshot, args.seven_zip)
        report = args.report
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
