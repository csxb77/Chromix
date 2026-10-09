#!/usr/bin/env python3
"""Reverify immutable Windows ARM64 artifacts without building or publishing."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

from download_posix_snapshot import GitHub, SnapshotError, require, require_space

REPO = Path(__file__).resolve().parents[1]
REPOSITORY = "xiaozhou26/Chromix"
REPOSITORY_ID = 1342691290
RUN_ID = 37852274649
ATTEMPT = 1
HEAD_SHA = "16927143fdd1d69b6f1874d4ad8186edfe5c0f92"
BRANCH = "build/windows154-arm64-stage13-20261008"
WORKFLOW = "build-win-arm64-stage8-recovery"
WORKFLOW_PATH = f".github/workflows/{WORKFLOW}.yml"
WORKFLOW_ID = 370810608
JOB_ID = 113567913897
JOB_NAME = "stage 13 (resume compile)"
BUILD_SHA = "15a6425cfbd7a7ecd4a69ad206d0650778bf8253"
BUILD_RUN = 37455471388
BUILD_JOB = 113438156537
VERSION = "154.0.8037.97"
BROWSER_SHA256 = "8f524a15ef3236d629a309f3df62e7fe69421e61ec36511640bb19592cbf26ed"
SOURCE_SHA256 = "84f78b2953c90093071b9a176ec2b495a1145a0dc799cf474393031c2ef882fe"
SERIES_SHA256 = "df6d11403b92894d9f04a0afa1082ad2be8e56901b67dbaff00d5cd8540cb7ed"
ARTIFACTS = (
    {"id": 11586044074, "name": "win-arm64", "size_in_bytes": 207791485,
     "digest": "sha256:c9de2147329003ba11f8e1d4599ffb6213342074c379236beec86ba6b32dcfcc", "expired": False},
    {"id": 11585814742, "name": "chromix-win-arm64-source-receipt", "size_in_bytes": 20752,
     "digest": "sha256:454e2f37530e1ba03cc427da76ad4502a0e78af1ebc8037a12ef0a8f3428f5d4", "expired": False},
    {"id": 11583321427, "name": "windows-arm64-stage13-recovery-proof-1", "size_in_bytes": 2112,
     "digest": "sha256:141d251b7ce61d703bb1dc5f0eac2ab19fabadeb8f621c36328db1b4d70a4735", "expired": False},
)
UPLOAD_STEPS = ("Upload final bundle", "Upload final source receipt", "Upload recovery proof diagnostics")
PROOF_FILES = {
    "windows-snapshot.json": (903, "b152712aa1b78521136b1eace69f1e361649c817f81b78175609d516e01a9e3f"),
    "windows-snapshot-download.json": (1834, "f076ec7fca40eacf0c485562d171f680cd00f8c99c148920250ccf77b9c406dc"),
    "windows-arm64-snapshot-proof.json": (207, "2fd75b8b8facdad62987d46dc80f35f530eab9bd3ddeb8f4c6bbf3237d2b50e4"),
    "windows-arm64-source-proof.json": (507, "6f6d00c6bc2a5940ae7efe307d0d1b80b26ae5a27927249e8c5bc7ea735cca55"),
}
RECOVERY_CHANGES = [
    ".github/workflows/build-win-arm64-stage8-recovery.yml",
    "tools/tests/test_windows_arm64_stage8_recovery.py",
    "tools/tests/test_windows_arm64_stage8_recovery_workflow.py",
    "tools/verify_windows_arm64_stage8_recovery.py",
]
LIMIT = 2 * 1024 * 1024


def matches(actual, expected, label):
    require(isinstance(actual, dict), label)
    for key, value in expected.items():
        require(type(actual.get(key)) is type(value) and actual[key] == value, label + ": " + key)


def dispatch_guard(event, repository, inputs):
    require(event == "workflow_dispatch" and repository == REPOSITORY, "wrong_dispatch_origin")
    require(type(inputs) is dict and not inputs, "dispatch_inputs_forbidden")


class Metadata(GitHub):
    def get(self, path):
        require(path.startswith("/actions/") and not any(c in path for c in "\r\n#"), "invalid_api_path")
        request = urllib.request.Request(f"https://api.github.com/repos/{REPOSITORY}" + path,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        request.add_unredirected_header("Authorization", "Bearer " + self.token)
        try:
            with self.opener.open(request, timeout=60) as response:
                require(response.status == 200, "metadata_http_status")
                data = response.read(LIMIT + 1)
        except (urllib.error.URLError, OSError):
            raise SnapshotError("metadata_request_failed") from None
        require(len(data) <= LIMIT, "metadata_size_limit")
        result = json.loads(data)
        require(isinstance(result, dict), "invalid_metadata")
        return result

    def items(self, path, key):
        result, total = [], None
        for page in range(1, 21):
            data = self.get(f"{path}?per_page=100&page={page}")
            count, batch = data.get("total_count"), data.get(key)
            require(type(count) is int and 0 <= count <= 2000 and isinstance(batch, list)
                    and all(isinstance(item, dict) for item in batch), "invalid_pagination")
            require(total is None or count == total, "metadata_changed_during_pagination")
            total = count
            result.extend(batch)
            if len(result) == total:
                return result
            require(batch and len(result) < total, "incomplete_pagination")
        raise SnapshotError("pagination_limit")


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value),
            "invalid_metadata_timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_metadata(client):
    path = f"/actions/runs/{RUN_ID}"
    runs = [client.get(path), client.get(path + f"/attempts/{ATTEMPT}")]
    for run in runs:
        matches(run, {"id": RUN_ID, "run_attempt": ATTEMPT, "name": WORKFLOW, "path": WORKFLOW_PATH,
                      "workflow_id": WORKFLOW_ID, "head_sha": HEAD_SHA, "head_branch": BRANCH,
                      "event": "workflow_dispatch", "status": "completed", "conclusion": "failure"},
                "donor_run_mismatch")
        for key in ("repository", "head_repository"):
            matches(run.get(key), {"full_name": REPOSITORY, "id": REPOSITORY_ID}, "donor_repository_mismatch")
    jobs = client.items(path + f"/attempts/{ATTEMPT}/jobs", "jobs")
    selected = [j for j in jobs if j.get("id") == JOB_ID or j.get("name") == JOB_NAME]
    require(len(selected) == 1, "missing_or_ambiguous_stage13_job")
    job = selected[0]
    matches(job, {"id": JOB_ID, "name": JOB_NAME, "run_id": RUN_ID, "run_attempt": ATTEMPT,
                  "head_sha": HEAD_SHA, "head_branch": BRANCH, "workflow_name": WORKFLOW,
                  "status": "completed", "conclusion": "success"}, "stage13_job_mismatch")
    steps = job.get("steps")
    require(isinstance(steps, list) and all(isinstance(s, dict) for s in steps), "invalid_build_steps")
    successful = {}
    for name in (*UPLOAD_STEPS, "Run stage 13", "Prove source and build inputs are unchanged",
                 "Validate ARM64 snapshot markers and receipts"):
        found = [s for s in steps if s.get("name") == name]
        require(len(found) == 1, "missing_or_duplicate_build_step")
        step = found[0]
        matches(step, {"status": "completed", "conclusion": "success"}, "unsuccessful_build_step")
        require(timestamp(job["started_at"]) <= timestamp(step["started_at"])
                <= timestamp(step["completed_at"]) <= timestamp(job["completed_at"]), "invalid_step_window")
        successful[name] = step
    artifacts = client.items(path + "/artifacts", "artifacts")
    selected = [a for a in artifacts if a.get("id") in {p["id"] for p in ARTIFACTS}
                or a.get("name") in {p["name"] for p in ARTIFACTS}]
    require(len(selected) == len(ARTIFACTS), "incomplete_or_ambiguous_artifacts")
    for pin, step_name in zip(ARTIFACTS, UPLOAD_STEPS):
        found = [a for a in selected if a.get("id") == pin["id"]]
        require(len(found) == 1, "missing_pinned_artifact")
        artifact, step = found[0], successful[step_name]
        matches(artifact, pin, "artifact_pin_mismatch")
        matches(artifact.get("workflow_run"), {"id": RUN_ID, "repository_id": REPOSITORY_ID,
                "head_repository_id": REPOSITORY_ID, "head_sha": HEAD_SHA, "head_branch": BRANCH},
                "artifact_origin_mismatch")
        require(timestamp(step["started_at"]) <= timestamp(artifact.get("created_at"))
                <= timestamp(artifact.get("updated_at")) <= timestamp(step["completed_at"]),
                "artifact_outside_successful_upload_window")
        if step_name != "Upload recovery proof diagnostics":
            require(timestamp(successful["Run stage 13"]["completed_at"]) <= timestamp(step["started_at"]),
                    "artifact_precedes_build_completion")
    return {"runs": runs, "build_job": job, "artifacts": selected, "original_run_conclusion": "failure"}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_file(path, size, digest):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == size, "file_size_mismatch")
    require(sha256(path) == digest, "file_digest_mismatch")


def extract_outer(path, destination, expected):
    require(not os.path.lexists(destination), "destination_exists")
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        require(len(members) == len(expected), "unexpected_outer_members")
        seen = set()
        for info in members:
            name = info.orig_filename
            require(name == info.filename and name in expected and name not in seen, "unsafe_outer_member")
            require(re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", name), "unsafe_outer_member")
            seen.add(name)
            require(not info.is_dir() and not info.external_attr & 0x10
                    and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG)
                    and not info.flag_bits & 1
                    and info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                    "non_regular_outer_member")
            require(0 < info.file_size <= expected[name], "outer_expansion_limit")
        require_space(destination.parent, sum(info.file_size for info in members))
        destination.mkdir()
        for info in members:
            size = 0
            with archive.open(info) as source, (destination / info.filename).open("xb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    size += len(chunk)
                    require(size <= info.file_size, "outer_member_size_mismatch")
                    output.write(chunk)
            require(size == info.file_size, "outer_member_size_mismatch")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], timeout=30,
                                   text=True, encoding="utf-8").strip()


def source_inputs(source, donor):
    from apply_restored_patches import transform_patch
    from fingerprint_acceptance import read_json
    from patch_selection import read

    receipt = read_json(source)
    identity = receipt["identity"]
    selection = identity["selection"]
    matches(selection, {"schema_version": 1, "version": VERSION, "platform": "windows",
            "core_commit": "37085e47cf580c815a30402917d350ce97399ded",
            "platform_commit": "f03c33d7974af5b40f25b01984ded8418d60fbe4",
            "base_series_sha256": "b4413124e3bf45bf6126ccf21d5ac66faef61f07cca9d4afad59324b3bfd3e98"},
            "historical_selection_mismatch")
    base = read(donor, "patches/series")
    require(hashlib.sha256(base).hexdigest() == selection["base_series_sha256"], "donor_base_series_mismatch")
    names = [line.split("#", 1)[0].strip() for line in base.decode("utf-8").splitlines()]
    names = [name for name in names if name]
    selected = identity["series"]["patches"]
    require(len(names) == len(set(names)) == len(selected) == 216, "donor_patch_count_mismatch")
    mapping, targets = {}, set()
    for name, entry in zip(names, selected):
        path = entry["path"]
        require(re.fullmatch(r"patches/[^/]+\.patch", name)
                and re.fullmatch(r"patches/(?:[^/]+/)*[^/]+\.patch", path)
                and Path(name).name == Path(path).name, "donor_patch_path_mismatch")
        raw = read(donor, path)
        require(hashlib.sha256(raw).hexdigest() == entry["sha256"], "donor_patch_hash_mismatch")
        _, entries = transform_patch(raw, set(), [])
        targets.update(item[0] for item in entries)
        mapping[name] = path
    effective = "".join(line.replace(name, mapping[name], 1)
                        if (name := line.split("#", 1)[0].strip()) else line
                        for line in base.decode("utf-8").splitlines(keepends=True)).encode("utf-8")
    require(hashlib.sha256(effective).hexdigest() == identity["series"]["sha256"] == SERIES_SHA256,
            "donor_effective_series_mismatch")
    return {"patches": selected, "series_sha256": SERIES_SHA256, "selection": selection}, targets


def source_identity(source, proof, donor):
    from fingerprint_acceptance import check_source, read_json

    verify_file(source, 65830, SOURCE_SHA256)
    for name, (size, digest) in PROOF_FILES.items():
        verify_file(proof / name, size, digest)
    snapshot = read_json(proof / "windows-snapshot.json")
    matches(snapshot, {"repository": REPOSITORY, "workflow": "build-win-arm64-github", "run_id": BUILD_RUN,
            "attempt": 1, "head_sha": BUILD_SHA, "head_branch": "main", "job_id": BUILD_JOB, "stage": 12},
            "original_build_identity_mismatch")
    matches(read_json(proof / "windows-snapshot-download.json"),
            {"status": "success", "head_sha": BUILD_SHA, "run_id": BUILD_RUN}, "snapshot_download_mismatch")
    matches(read_json(proof / "windows-arm64-snapshot-proof.json"),
            {"status": "verified", "platform": "windows", "architecture": "arm64", "source_ready": True,
             "restored_patches": 216}, "snapshot_proof_mismatch")
    recovery = read_json(proof / "windows-arm64-source-proof.json")
    matches(recovery, {"status": "verified", "operation": "windows-arm64-stage8-source-proof",
            "previous_sha": BUILD_SHA, "target_sha": HEAD_SHA, "source_inputs_changed": [],
            "changed_files": RECOVERY_CHANGES}, "recovery_source_proof_mismatch")
    require(donor.resolve() != REPO and git(donor, "rev-parse", "HEAD") == HEAD_SHA, "wrong_donor_checkout")
    require(not git(donor, "status", "--porcelain", "--untracked-files=all"), "dirty_donor_checkout")
    require(git(donor, "rev-list", "--parents", "-n", "1", HEAD_SHA).split() == [HEAD_SHA, BUILD_SHA],
            "recovery_parent_mismatch")
    require(sorted(git(donor, "diff", "--name-only", BUILD_SHA, HEAD_SHA).splitlines()) == RECOVERY_CHANGES,
            "recovery_changed_inputs")
    inputs, targets = source_inputs(source, donor)
    receipt = read_json(source)
    matches(receipt, {"schema_version": 1, "status": "verified", "method": "reverse-forward-in-scratch",
                     "domain_substituted": True, "patch_count": 216}, "source_receipt_mismatch")
    matches(receipt.get("identity"), {"schema_version": 1, "platform": "windows"}, "source_identity_mismatch")
    matches(receipt["identity"].get("selection"), {"version": VERSION, "platform": "windows"},
            "source_selection_mismatch")
    checked = check_source(source, None, inputs, targets)
    return {**checked, "original_build_sha": BUILD_SHA, "original_build_run": BUILD_RUN,
            "recovery_sha": HEAD_SHA, "recovery_run": RUN_ID, "recovery_attempt": ATTEMPT,
            "series_sha256": SERIES_SHA256, "patch_count": len(inputs["patches"]),
            "binding": "pinned receipt bytes, successful stage13 upload and read-only recovery proof",
            "qualification": "producer receipt only; not live-source replay or signed binary/source attestation"}


def verifier_identity():
    head = git(REPO, "rev-parse", "HEAD")
    require(re.fullmatch(r"[0-9a-f]{40}", head) and head == os.environ.get("GITHUB_SHA"),
            "verifier_checkout_mismatch")
    require(not git(REPO, "status", "--porcelain", "--untracked-files=no"), "dirty_verifier_checkout")
    return {"sha": head, "run_id": os.environ.get("GITHUB_RUN_ID"),
            "attempt": os.environ.get("GITHUB_RUN_ATTEMPT"), "helper_sha256": sha256(Path(__file__))}


def write_new(path, report):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")


def prepare(directory, donor, report):
    report["verifier"] = verifier_identity()
    report["phase"] = "metadata"
    client = Metadata()
    report["donor"] = validate_metadata(client)
    downloads = directory / "downloads"
    downloads.mkdir()
    diagnostics = directory / "diagnostics"
    destinations = (downloads / "bundle", diagnostics / "source-receipt", diagnostics / "recovery-proof")
    layouts = ({"chromix-win-arm64.zip": ARTIFACTS[0]["size_in_bytes"], "SHA256SUMS": 65536},
               {"source-verification.json": 65830}, {name: size for name, (size, _) in PROOF_FILES.items()})
    report["downloads"] = []
    deadline = time.monotonic() + 1200
    for artifact, destination, layout in zip(ARTIFACTS, destinations, layouts):
        report["phase"] = "download_" + artifact["name"]
        record = {**artifact, "attempts": []}
        report["downloads"].append(record)
        outer = client.download(REPOSITORY, artifact, downloads, deadline, record)
        verify_file(outer, artifact["size_in_bytes"], artifact["digest"].removeprefix("sha256:"))
        extract_outer(outer, destination, layout)
        outer.unlink()
    report["phase"] = "source_identity"
    report["source"] = source_identity(destinations[1] / "source-verification.json", destinations[2], donor)
    report.update(status="prepared", phase="complete", browser_sha256=BROWSER_SHA256, version=VERSION)


def acceptance(directory, donor, report):
    import fingerprint_acceptance as gate

    diagnostics = directory / "diagnostics"
    prepared = gate.read_json(diagnostics / "preparation.json")
    report["verifier"] = verifier_identity()
    require(prepared.get("status") == "prepared" and prepared.get("verifier") == report["verifier"],
            "preparation_not_verified")
    source = diagnostics / "source-receipt/source-verification.json"
    proof = diagnostics / "recovery-proof"
    report["source"] = source_identity(source, proof, donor)
    require(report["source"] == prepared.get("source"), "source_changed_after_preparation")
    browser = directory / "smoke/chromix/chrome.exe"
    require(sha256(browser) == BROWSER_SHA256, "browser_hash_mismatch")
    smoke = gate.read_json(diagnostics / "native-smoke.json")
    matches(smoke, {"arch": "arm64"}, "native_smoke_arch_mismatch")
    matches(smoke.get("runtime"), {"status": "passed", "version": VERSION, "native_arch": "arm64"},
            "native_smoke_not_passed")
    matches(smoke.get("static"), {"status": "passed"}, "static_smoke_not_passed")
    require(Path(smoke["bundle_dir"]).resolve() == browser.parent.resolve(), "smoke_browser_path_mismatch")
    require(smoke["static"]["pe_files"]["chrome.exe"]["sha256"] == BROWSER_SHA256, "smoke_browser_hash_mismatch")
    require(sys.platform == "win32", "native_windows_required")
    inputs, source_targets = source_inputs(source, donor)
    targets = sorted(source_targets)
    current_provenance = gate.provenance

    def historical_source_provenance():
        # Keep current probe/package/commit identity; only source inputs belong to the donor.
        identity = current_provenance()
        identity.update(patch_inputs=inputs, patch_targets=targets, source_repository_commit=HEAD_SHA,
                        source_build_commit=BUILD_SHA)
        return identity

    report["phase"] = "fingerprint_acceptance"
    gate.provenance = historical_source_provenance
    try:
        code = gate.main(["--browser", str(browser), "--expected-sha256", BROWSER_SHA256,
                          "--expected-version", VERSION, "--source-report", str(source),
                          "--output-dir", str(diagnostics / "fingerprint")])
    finally:
        gate.provenance = current_provenance
    observed = gate.read_json(diagnostics / "fingerprint/acceptance.json")
    report["acceptance"] = {key: observed.get(key) for key in
                            ("status", "ci_gate_passed", "full_acceptance", "errors", "gaps", "qualification")}
    require(sha256(browser) == BROWSER_SHA256 and source_identity(source, proof, donor) == report["source"],
            "inputs_changed_during_acceptance")
    require(code == 0 and observed.get("ci_gate_passed") is True and observed.get("control") is False
            and observed.get("errors") == [], "fingerprint_acceptance_failed")
    require([s.get("name") for s in observed.get("suites", [])] == [s[0] for s in gate.SUITES],
            "incomplete_acceptance_suites")
    require(verifier_identity() == report["verifier"], "verifier_changed_during_acceptance")
    report.update(status="reverified", phase="complete", ci_gate_passed=True,
                  browser_sha256=BROWSER_SHA256, version=VERSION)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "acceptance"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--donor", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {"schema_version": 1, "status": "failed", "phase": "guard", "ci_gate_passed": False,
              "original_run_id": RUN_ID, "original_run_conclusion": "failure", "publication": "not_requested"}
    output = None
    try:
        dispatch_guard(os.environ.get("GITHUB_EVENT_NAME"), os.environ.get("GITHUB_REPOSITORY"),
                       json.loads(os.environ.get("REVERIFY_INPUTS", "{}")))
        require(args.directory.is_absolute() and not args.directory.is_symlink(), "absolute_directory_required")
        if args.operation == "prepare":
            args.directory.mkdir()
            (args.directory / "diagnostics").mkdir()
        output = args.directory / "diagnostics" / (
            "preparation.json" if args.operation == "prepare" else "reverified.json")
        require(not os.path.lexists(output), "report_already_exists")
        (prepare if args.operation == "prepare" else acceptance)(args.directory, args.donor, report)
    except Exception as error:
        report.update(status="failed", ci_gate_passed=False,
                      reason=str(error) if isinstance(error, SnapshotError) else type(error).__name__)
        print("ARM64 re-verification failed: " + report["reason"], file=sys.stderr)
    finally:
        if output is not None and not os.path.lexists(output):
            write_new(output, report)
    return int(report["status"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
