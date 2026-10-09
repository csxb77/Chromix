"""Fail-closed contracts for the fixed Windows 154 ARM64 stage-12 donor."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import download_windows_snapshot as downloader
import validate_windows154_arm64_checkpoint as checkpoint


class Metadata:
    def __init__(self):
        self.calls = []
        self.run = {"id": checkpoint.RUN_ID, "path": f".github/workflows/{checkpoint.WORKFLOW}.yml",
                    "event": "workflow_dispatch", "head_branch": "main", "head_sha": checkpoint.DONOR_SHA,
                    "status": "completed", "conclusion": "failure", "run_attempt": 1,
                    "repository": {"full_name": checkpoint.REPOSITORY},
                    "head_repository": {"full_name": checkpoint.REPOSITORY}}
        self.jobs = [{"id": checkpoint.JOB_ID, "name": "stage 12 (resume compile)",
                      "status": "completed", "conclusion": "failure", "run_id": checkpoint.RUN_ID,
                      "run_attempt": 1, "head_sha": checkpoint.DONOR_SHA,
                      "steps": [{"name": name, "status": "completed", "conclusion": "success"}
                                for name in ("Ensure build tree snapshot", "Upload tree part 1", "Upload tree part 2")]}]
        self.artifacts = [{**item, "workflow_run": {"id": checkpoint.RUN_ID,
                           "head_sha": checkpoint.DONOR_SHA, "head_branch": "main"}}
                          for item in checkpoint.ARTIFACTS]

    def get(self, path):
        self.calls.append(path)
        assert path == "/actions/runs/37455471388"
        return self.run

    def items(self, path, key):
        self.calls.append(path)
        assert path == {"jobs": "/actions/runs/37455471388/attempts/1/jobs",
                        "artifacts": "/actions/runs/37455471388/artifacts"}[key]
        return getattr(self, key)


def test_fixed_failure_checkpoint_is_download_manifest(tmp_path):
    client = Metadata()
    manifest = checkpoint.validate_metadata(client)
    assert manifest["job_id"] == 113438156537
    assert manifest["artifacts"] == list(checkpoint.ARTIFACTS)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert downloader.load_manifest(path)["run_id"] == 37455471388
    assert client.calls == ["/actions/runs/37455471388", "/actions/runs/37455471388/attempts/1/jobs",
                            "/actions/runs/37455471388/artifacts"]


@pytest.mark.parametrize("field,value", [
    ("id", 1), ("path", ".github/workflows/build-win-arm64-stage8-recovery.yml"),
    ("head_branch", "recovery"), ("head_sha", "0" * 40), ("run_attempt", 2),
    ("run_attempt", True), ("status", "in_progress"), ("conclusion", "success"),
    ("event", "pull_request"), ("repository", {"full_name": "fork/Chromix"}),
    ("head_repository", {"full_name": "fork/Chromix"}), ("repository", None),
])
def test_run_identity_cannot_be_substituted(field, value):
    client = Metadata()
    client.run[field] = value
    with pytest.raises(ValueError):
        checkpoint.validate_metadata(client)


@pytest.mark.parametrize("field,value", [
    ("id", 113438156538), ("name", "stage 11 (resume compile)"), ("run_id", 37455471389),
    ("run_attempt", True), ("run_attempt", 2), ("head_sha", "0" * 40),
    ("status", "in_progress"), ("conclusion", "success"),
])
def test_job_identity_cannot_be_substituted(field, value):
    client = Metadata()
    client.jobs[0][field] = value
    with pytest.raises(ValueError):
        checkpoint.validate_metadata(client)


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("mutation", ("missing", "duplicate", "failed", "incomplete"))
def test_required_checkpoint_steps_are_unambiguous_successes(index, mutation):
    client = Metadata()
    steps = client.jobs[0]["steps"]
    if mutation == "missing":
        del steps[index]
    elif mutation == "duplicate":
        steps.append(copy.deepcopy(steps[index]))
    elif mutation == "failed":
        steps[index]["conclusion"] = "failure"
    else:
        steps[index]["status"] = "in_progress"
    with pytest.raises(ValueError):
        checkpoint.validate_metadata(client)


@pytest.mark.parametrize("field,value", [
    ("id", 1), ("name", checkpoint.PREFIX + "3"), ("expired", True), ("expired", 0),
    ("size_in_bytes", 1), ("size_in_bytes", "9663676664"), ("digest", "sha256:" + "0" * 64),
    ("workflow_run", {"id": checkpoint.RUN_ID, "head_sha": "0" * 40, "head_branch": "main"}),
    ("workflow_run", {"id": checkpoint.RUN_ID, "head_sha": checkpoint.DONOR_SHA, "head_branch": "fork"}),
])
@pytest.mark.parametrize("index", (0, 1))
def test_artifact_identity_is_immutable(index, field, value):
    client = Metadata()
    client.artifacts[index][field] = value
    with pytest.raises(ValueError):
        checkpoint.validate_metadata(client)


@pytest.mark.parametrize("collection", ("jobs", "artifacts"))
@pytest.mark.parametrize("mutation", ("missing", "duplicate", "invalid"))
def test_metadata_sets_fail_closed(collection, mutation):
    client = Metadata()
    if mutation == "missing":
        getattr(client, collection).pop()
    elif mutation == "duplicate":
        getattr(client, collection).append(copy.deepcopy(getattr(client, collection)[0]))
    else:
        setattr(client, collection, [None])
    with pytest.raises(ValueError):
        checkpoint.validate_metadata(client)


def test_repository_argument_cannot_redirect_metadata():
    client = Metadata()
    with pytest.raises(ValueError, match="repository"):
        checkpoint.validate_metadata(client, "fork/Chromix")
    assert client.calls == []


def snapshot_files():
    return {name: value.encode() for name, value in {
        ".chromix-target-arch": "arm64\n",
        "src/chrome/VERSION": "MAJOR=154\nMINOR=0\nBUILD=8037\nPATCH=97\n",
        "src/.chromix-upstream-restored.json": json.dumps({"status": "restored", "platform": "windows",
            "arch": "arm64", "identity": {"arch": "arm64"}}),
        "src/.chromix-restored-patches.json": json.dumps({"schema_version": 1, "identity_sha256": "a" * 64,
            "outputs": {"source.cc": "b" * 64}, "identity": {"platform": "windows",
            "selection": {"version": checkpoint.VERSION}, "series": {"patches": [{"path": "fixture.patch"}]}}}),
        "src/.chromix-source-ready": checkpoint.VERSION + "|pins|patches",
        "src/.chromix-source-unpacked": checkpoint.VERSION,
        "src/out/Default/args.gn": 'target_cpu = "arm64"\ntarget_os = "win"\n',
    }.items()}


def listing(files=None, extra=()):
    files = snapshot_files() if files is None else files
    return "\n\n".join([f"Path = chromix/{name}\nSize = {len(data)}\nFolder = -\nAttributes = A"
                          for name, data in files.items()] + list(extra)) + "\n"


@pytest.mark.parametrize("name,data", [
    (".chromix-target-arch", b"x64"), ("src/chrome/VERSION", b"MAJOR=154\nMINOR=0\nBUILD=8037\nPATCH=57"),
    ("src/.chromix-source-ready", b"154.0.8037.57|wrong"), ("src/.chromix-source-unpacked", b"154.0.8037.57"),
    ("src/.chromix-upstream-restored.json", b"null"), ("src/.chromix-restored-patches.json", b"{}"),
    ("src/out/Default/args.gn", b'target_cpu = "x64"\ntarget_os = "win"'),
    ("src/out/Default/args.gn", b'target_cpu = "arm64"\ntarget_os = "win"\ntarget_os = "linux"'),
])
def test_snapshot_markers_cannot_change_version_or_architecture(name, data):
    files = snapshot_files()
    assert checkpoint.validate_snapshot_files(files)["version"] == "154.0.8037.97"
    files[name] = data
    with pytest.raises(ValueError):
        checkpoint.validate_snapshot_files(files)


@pytest.mark.parametrize("path", [
    "../outside", "C:/outside", "chromix/../outside", "chromix/src/file:stream", "chromix/src/CON.txt",
    "chromix/src/trailing.", "chromix/src/out/Chromix/build.ninja",
    "chromix/src/.chromix-restored-patches-in-progress", "chromix/src/.chromix-layer-in-progress",
    "chromix/.chromix-upstream-restore-interrupted/state.json", "chromix/src/chrome/version",
])
def test_archive_paths_cannot_escape_or_hide_invalid_state(path):
    with pytest.raises(ValueError):
        checkpoint.validate_archive_listing(listing(extra=[f"Path = {path}\nSize = 1\nFolder = -"]))


@pytest.mark.parametrize("field", ["Symbolic Link = ../escape", "Hard Link = ../escape", "Reparse = +",
                                    "Attributes = A lrwxrwxrwx", "Anti = +"])
def test_archive_links_and_deletions_rejected(field):
    with pytest.raises(ValueError):
        checkpoint.validate_archive_listing(listing(extra=[f"Path = chromix/link\nSize = 1\n{field}"]))


def test_archive_missing_and_oversized_markers_rejected():
    files = snapshot_files()
    del files["src/chrome/VERSION"]
    with pytest.raises(ValueError, match="marker"):
        checkpoint.validate_archive_listing(listing(files))
    with pytest.raises(ValueError, match="marker"):
        checkpoint.validate_archive_listing(listing().replace("Size = 6\n", "Size = 999999999\n", 1))


def test_archive_inspection_reads_only_fixed_markers(monkeypatch, tmp_path):
    calls = []
    files = snapshot_files()

    def run(command, **kwargs):
        calls.append(command)
        data = listing().encode() if command[1] == "l" else files[command[3].removeprefix("chromix/")]
        return subprocess.CompletedProcess(command, 0, stdout=data)

    monkeypatch.setattr(checkpoint.subprocess, "run", run)
    report = checkpoint.verify_snapshot_archive(tmp_path / "tree.7z.001", "7z")
    assert report["status"] == "verified"
    assert len(calls) == len(checkpoint.MARKERS) + 1
    assert all(command[1] in ("l", "e") for command in calls)
    assert "-ba" in calls[0] and "-sccUTF-8" in calls[0]
