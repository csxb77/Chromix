"""Immutable artifact metadata, receipt binding and safe extraction contracts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reverify_windows_arm64_artifacts as recovery


class MetadataFixture:
    def __init__(self):
        self.run = {"id": recovery.RUN_ID, "run_attempt": 1, "name": recovery.WORKFLOW,
                    "path": recovery.WORKFLOW_PATH, "workflow_id": recovery.WORKFLOW_ID,
                    "head_sha": recovery.HEAD_SHA, "head_branch": recovery.BRANCH,
                    "event": "workflow_dispatch", "status": "completed", "conclusion": "failure",
                    "repository": {"full_name": recovery.REPOSITORY, "id": recovery.REPOSITORY_ID},
                    "head_repository": {"full_name": recovery.REPOSITORY, "id": recovery.REPOSITORY_ID}}
        self.attempt = deepcopy(self.run)
        self.job = {"id": recovery.JOB_ID, "name": recovery.JOB_NAME, "run_id": recovery.RUN_ID,
                    "run_attempt": 1, "head_sha": recovery.HEAD_SHA, "head_branch": recovery.BRANCH,
                    "workflow_name": recovery.WORKFLOW, "status": "completed", "conclusion": "success",
                    "started_at": "2026-10-08T22:15:17Z", "completed_at": "2026-10-09T00:00:02Z",
                    "steps": []}
        times = [("23:59:53", "23:59:56"), ("23:59:56", "23:59:57"), ("22:25:21", "22:25:22"),
                 ("22:25:23", "23:59:51"), ("22:21:48", "22:21:49"), ("22:20:48", "22:21:48")]
        names = (*recovery.UPLOAD_STEPS, "Run stage 13", "Prove source and build inputs are unchanged",
                 "Validate ARM64 snapshot markers and receipts")
        for name, (start, end) in zip(names, times):
            self.job["steps"].append({"name": name, "status": "completed", "conclusion": "success",
                                      "started_at": "2026-10-08T" + start + "Z",
                                      "completed_at": "2026-10-08T" + end + "Z"})
        self.jobs = [self.job]
        self.artifacts = []
        for pin, (_, end) in zip(recovery.ARTIFACTS, times):
            self.artifacts.append({**pin, "created_at": "2026-10-08T" + end + "Z",
                                   "updated_at": "2026-10-08T" + end + "Z",
                                   "workflow_run": {"id": recovery.RUN_ID, "head_sha": recovery.HEAD_SHA,
                                       "head_branch": recovery.BRANCH, "repository_id": recovery.REPOSITORY_ID,
                                       "head_repository_id": recovery.REPOSITORY_ID}})

    def get(self, path):
        return self.attempt if path.endswith("/attempts/1") else self.run

    def items(self, path, key):
        return self.jobs if key == "jobs" else self.artifacts


def test_exact_pins_and_successful_job_from_failed_run():
    assert recovery.RUN_ID == 37852274649
    assert recovery.HEAD_SHA == "16927143fdd1d69b6f1874d4ad8186edfe5c0f92"
    assert recovery.BRANCH == "build/windows154-arm64-stage13-20261008"
    assert recovery.JOB_ID == 113567913897
    assert recovery.WORKFLOW_PATH == ".github/workflows/build-win-arm64-stage8-recovery.yml"
    assert [(a["id"], a["name"], a["size_in_bytes"], a["digest"]) for a in recovery.ARTIFACTS[:2]] == [
        (11586044074, "win-arm64", 207791485,
         "sha256:c9de2147329003ba11f8e1d4599ffb6213342074c379236beec86ba6b32dcfcc"),
        (11585814742, "chromix-win-arm64-source-receipt", 20752,
         "sha256:454e2f37530e1ba03cc427da76ad4502a0e78af1ebc8037a12ef0a8f3428f5d4")]
    assert recovery.BROWSER_SHA256 == "8f524a15ef3236d629a309f3df62e7fe69421e61ec36511640bb19592cbf26ed"
    assert recovery.VERSION == "154.0.8037.97"
    report = recovery.validate_metadata(MetadataFixture())
    assert report["original_run_conclusion"] == "failure"
    assert report["build_job"]["conclusion"] == "success"


@pytest.mark.parametrize("inputs", [{"run_id": "1"}, {"compile": False}, [], None, ""])
def test_dispatch_has_no_inputs(inputs):
    with pytest.raises(ValueError, match="dispatch_inputs_forbidden"):
        recovery.dispatch_guard("workflow_dispatch", recovery.REPOSITORY, inputs)


def test_dispatch_origin():
    recovery.dispatch_guard("workflow_dispatch", recovery.REPOSITORY, {})
    for event, repo in [("push", recovery.REPOSITORY), ("workflow_dispatch", "fork/Chromix")]:
        with pytest.raises(ValueError, match="wrong_dispatch_origin"):
            recovery.dispatch_guard(event, repo, {})


@pytest.mark.parametrize("target,key,value", [
    ("run", "id", 1), ("run", "run_attempt", 2), ("attempt", "run_attempt", True),
    ("run", "head_sha", "0" * 40), ("attempt", "head_branch", "main"),
    ("run", "path", ".github/workflows/build-win-arm64-github.yml"),
    ("run", "workflow_id", 1), ("run", "event", "push"), ("run", "status", "in_progress"),
    ("run", "conclusion", "success"), ("attempt", "conclusion", "cancelled"),
    ("run", "head_repository", {"full_name": "fork/Chromix", "id": recovery.REPOSITORY_ID}),
    ("job", "id", 1), ("job", "name", "stage 12 (resume compile)"),
    ("job", "run_id", 1), ("job", "run_attempt", 2), ("job", "head_sha", "0" * 40),
    ("job", "head_branch", "main"), ("job", "conclusion", "failure"), ("job", "status", "queued"),
])
def test_metadata_fails_closed(target, key, value):
    client = MetadataFixture()
    getattr(client, target)[key] = value
    with pytest.raises(ValueError):
        recovery.validate_metadata(client)


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("key,value", [("id", 1), ("name", "wrong"), ("size_in_bytes", 1),
    ("digest", None), ("digest", "sha256:" + "0" * 64), ("expired", True), ("expired", 0),
    ("created_at", "2026-10-08T01:00:00Z"), ("updated_at", "2026-10-09T12:00:00Z")])
def test_artifact_pins_and_upload_window(index, key, value):
    client = MetadataFixture()
    client.artifacts[index][key] = value
    with pytest.raises(ValueError):
        recovery.validate_metadata(client)


@pytest.mark.parametrize("key,value", [("id", 1), ("head_sha", "0" * 40), ("head_branch", "main"),
                                       ("repository_id", 1), ("head_repository_id", 1)])
def test_artifact_source_metadata(key, value):
    client = MetadataFixture()
    client.artifacts[0]["workflow_run"][key] = value
    with pytest.raises(ValueError, match="artifact_origin_mismatch"):
        recovery.validate_metadata(client)


@pytest.mark.parametrize("mutation", ["missing_artifact", "duplicate_artifact", "duplicate_name",
                                     "missing_job", "duplicate_job", "failed_step", "missing_step", "duplicate_step"])
def test_incomplete_or_ambiguous_metadata(mutation):
    client = MetadataFixture()
    if mutation == "missing_artifact":
        client.artifacts.pop()
    elif mutation == "duplicate_artifact":
        client.artifacts.append(deepcopy(client.artifacts[0]))
    elif mutation == "duplicate_name":
        client.artifacts.append({**client.artifacts[0], "id": 999})
    elif mutation == "missing_job":
        client.jobs.clear()
    elif mutation == "duplicate_job":
        client.jobs.append(deepcopy(client.job))
    elif mutation == "failed_step":
        client.job["steps"][0]["conclusion"] = "failure"
    elif mutation == "missing_step":
        client.job["steps"].pop()
    else:
        client.job["steps"].append(deepcopy(client.job["steps"][0]))
    with pytest.raises(ValueError):
        recovery.validate_metadata(client)


def test_file_size_and_digest_fail_closed(tmp_path):
    path = tmp_path / "artifact.zip"
    path.write_bytes(b"pinned bytes")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    recovery.verify_file(path, 12, digest)
    with pytest.raises(ValueError, match="file_size_mismatch"):
        recovery.verify_file(path, 11, digest)
    with pytest.raises(ValueError, match="file_digest_mismatch"):
        recovery.verify_file(path, 12, "0" * 64)
    path.write_bytes(b"edited bytes")
    with pytest.raises(ValueError, match="file_digest_mismatch"):
        recovery.verify_file(path, 12, digest)


@pytest.mark.parametrize("name", ["../source-verification.json", "/source-verification.json",
    "C:/source-verification.json", "..\\source-verification.json", "source-verification.json:ads",
    "source-verification.json.", "SOURCE-VERIFICATION.JSON", "source-verification.json/", "NUL"])
def test_unsafe_zip_paths_fail_before_extraction(tmp_path, name):
    path = tmp_path / "outer.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"{}")
    destination = tmp_path / "output"
    with pytest.raises(ValueError):
        recovery.extract_outer(path, destination, {"source-verification.json": 100})
    assert not destination.exists()


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO, stat.S_IFCHR])
def test_non_regular_zip_entries(tmp_path, mode):
    path = tmp_path / "outer.zip"
    entry = zipfile.ZipInfo("source-verification.json")
    entry.external_attr = (mode | 0o600) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(entry, b"{}")
    with pytest.raises(ValueError, match="non_regular_outer_member"):
        recovery.extract_outer(path, tmp_path / "output", {entry.filename: 100})


def test_zip_duplicates_extra_entries_and_expansion(tmp_path):
    for names, limit in [(["source-verification.json"] * 2, 10),
                         (["source-verification.json", "unexpected.txt"], 10),
                         (["source-verification.json"], 1)]:
        path = tmp_path / "outer.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name in names:
                archive.writestr(name, b"{}")
        with pytest.raises(ValueError):
            recovery.extract_outer(path, tmp_path / "output", {"source-verification.json": limit})
        assert not (tmp_path / "output").exists()


def test_successful_extract_preserves_bytes_and_refuses_overwrite(tmp_path):
    path = tmp_path / "outer.zip"
    files = {"chromix-win-arm64.zip": b"inner zip bytes", "SHA256SUMS": b"checksum\n"}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    destination = tmp_path / "output"
    layout = {name: len(data) for name, data in files.items()}
    recovery.extract_outer(path, destination, layout)
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == files
    with pytest.raises(ValueError, match="destination_exists"):
        recovery.extract_outer(path, destination, layout)


def test_new_reports_never_rewrite_old_failure(tmp_path):
    path = tmp_path / "acceptance.json"
    recovery.write_new(path, {"status": "failed"})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        recovery.write_new(path, {"status": "passed"})
    assert path.read_bytes() == original


def test_preparation_digest_failure_never_extracts(tmp_path, monkeypatch):
    directory = tmp_path / "session"
    directory.mkdir()
    (directory / "diagnostics").mkdir()
    client = MetadataFixture()

    def corrupt_download(repository, artifact, staging, deadline, record):
        path = staging / "bad.zip"
        path.write_bytes(b"invalid archive")
        return path

    client.download = corrupt_download
    monkeypatch.setattr(recovery, "Metadata", lambda: client)
    monkeypatch.setattr(recovery, "verifier_identity", lambda: {})
    monkeypatch.setattr(recovery, "extract_outer", lambda *args: pytest.fail("unverified ZIP extracted"))
    with pytest.raises(ValueError, match="file_size_mismatch"):
        recovery.prepare(directory, tmp_path / "donor", {})


def test_source_receipt_bytes_are_pinned_before_identity_checks(tmp_path):
    source = tmp_path / "source-verification.json"
    source.write_text(json.dumps({"status": "verified"}), encoding="utf-8")
    with pytest.raises(ValueError, match="file_size_mismatch"):
        recovery.source_identity(source, tmp_path, tmp_path / "donor")


def test_complete_preparation_uses_only_fixed_artifacts(tmp_path, monkeypatch):
    directory = tmp_path / "session"
    directory.mkdir()
    (directory / "diagnostics").mkdir()
    client = MetadataFixture()
    downloaded, extracted = [], []

    def download(repository, artifact, staging, deadline, record):
        assert repository == recovery.REPOSITORY
        downloaded.append(artifact["id"])
        path = staging / (str(artifact["id"]) + ".zip")
        path.write_bytes(b"verified")
        return path

    def extract(path, destination, expected):
        extracted.append((destination, expected))
        destination.mkdir()

    client.download = download
    monkeypatch.setattr(recovery, "Metadata", lambda: client)
    monkeypatch.setattr(recovery, "verifier_identity", lambda: {"sha": "current"})
    monkeypatch.setattr(recovery, "verify_file", lambda *args: None)
    monkeypatch.setattr(recovery, "extract_outer", extract)
    monkeypatch.setattr(recovery, "source_identity", lambda *args: {"sha256": recovery.SOURCE_SHA256})
    report = {}
    recovery.prepare(directory, tmp_path / "donor", report)
    assert downloaded == [11586044074, 11585814742, 11583321427]
    assert report["status"] == "prepared"
    assert report["donor"]["original_run_conclusion"] == "failure"
    assert extracted[1][1] == {"source-verification.json": 65830}
    assert extracted[2][1] == {name: size for name, (size, _) in recovery.PROOF_FILES.items()}


@pytest.mark.parametrize("gate_success", [True, False])
def test_acceptance_binds_historical_source_but_keeps_current_verifier(tmp_path, monkeypatch, gate_success):
    import fingerprint_acceptance as gate
    from types import SimpleNamespace

    directory = tmp_path / "session"
    diagnostics = directory / "diagnostics"
    diagnostics.mkdir(parents=True)
    browser = directory / "smoke/chromix/chrome.exe"
    browser.parent.mkdir(parents=True)
    browser.write_bytes(b"pinned browser")
    verifier = {"sha": "current-verifier"}
    source = {"sha256": recovery.SOURCE_SHA256}
    recovery.write_new(diagnostics / "preparation.json",
                       {"status": "prepared", "verifier": verifier, "source": source})
    recovery.write_new(diagnostics / "native-smoke.json", {
        "arch": "arm64", "bundle_dir": str(browser.parent),
        "runtime": {"status": "passed", "version": recovery.VERSION, "native_arch": "arm64"},
        "static": {"status": "passed", "pe_files": {"chrome.exe": {"sha256": recovery.BROWSER_SHA256}}}})
    monkeypatch.setattr(recovery, "verifier_identity", lambda: verifier)
    monkeypatch.setattr(recovery, "source_identity", lambda *args: source)
    monkeypatch.setattr(recovery, "source_inputs", lambda *args: ({"historical": True}, {"old/target.cc"}))
    monkeypatch.setattr(recovery, "sha256", lambda path: recovery.BROWSER_SHA256)
    monkeypatch.setattr(recovery, "sys", SimpleNamespace(platform="win32"))
    current = lambda: {"commit": "current-verifier", "runner_files": {"probe": "current"},
                       "patch_inputs": {"current": True}, "patch_targets": ["new/target.cc"]}
    monkeypatch.setattr(gate, "provenance", current)

    def acceptance(argv):
        assert argv == ["--browser", str(browser), "--expected-sha256", recovery.BROWSER_SHA256,
                        "--expected-version", recovery.VERSION, "--source-report",
                        str(diagnostics / "source-receipt/source-verification.json"),
                        "--output-dir", str(diagnostics / "fingerprint")]
        identity = gate.provenance()
        assert identity["commit"] == "current-verifier" and identity["runner_files"] == {"probe": "current"}
        assert identity["patch_inputs"] == {"historical": True}
        assert identity["patch_targets"] == ["old/target.cc"]
        assert identity["source_repository_commit"] == recovery.HEAD_SHA
        assert identity["source_build_commit"] == recovery.BUILD_SHA
        output = diagnostics / "fingerprint"
        output.mkdir()
        recovery.write_new(output / "acceptance.json", {
            "status": "incomplete" if gate_success else "failed", "ci_gate_passed": gate_success,
            "control": False, "full_acceptance": False, "errors": [] if gate_success else ["quic failed"],
            "gaps": ["optional capability"], "suites": [{"name": s[0]} for s in gate.SUITES]})
        return 0 if gate_success else 1

    monkeypatch.setattr(gate, "main", acceptance)
    report = {}
    if gate_success:
        recovery.acceptance(directory, tmp_path / "donor", report)
        assert report["status"] == "reverified" and report["ci_gate_passed"] is True
        assert report["acceptance"]["status"] == "incomplete"
        assert report["acceptance"]["full_acceptance"] is False
    else:
        with pytest.raises(ValueError, match="fingerprint_acceptance_failed"):
            recovery.acceptance(directory, tmp_path / "donor", report)
        assert report["acceptance"]["errors"] == ["quic failed"]
    assert gate.provenance is current


def test_workflow_is_read_only_pinned_and_native():
    text = (recovery.REPO / ".github/workflows/reverify-win-arm64.yml").read_text()
    assert "workflow_dispatch: {}" in text
    assert "contents: read" in text and "actions: read" in text and ": write" not in text
    assert "runs-on: windows-11-arm" in text
    assert "python-version: '3.13'" in text and "architecture: x64" in text
    assert f"ref: {recovery.HEAD_SHA}" in text
    assert "fetch-depth: 2" in text and text.count("persist-credentials: false") == 2
    assert "--arch arm64 --native --version " + recovery.VERSION in text
    assert "-r tools/fingerprint-requirements.txt" in text
    assert "reverify_windows_arm64_artifacts.py acceptance" in text
    assert "if: ${{ always() }}" in text and "retention-days: 7" in text
    assert "path: ${{ runner.temp }}/arm64-reverify/diagnostics/" in text
    for forbidden in ("ci-stage", "ninja", "workflow_call", "download-artifact", "gh release",
                      "continue-on-error", "build_mode", "resume_run_id", "pytest", "secrets."):
        assert forbidden not in text
