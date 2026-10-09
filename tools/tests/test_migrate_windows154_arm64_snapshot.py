"""Pinned selection and real Git scratch/transaction tests; no donor code executes."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import migrate_windows154_arm64_snapshot as migration

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="host Git required")


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def patch(name, old, new):
    return (f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n"
            f"@@ -1 +1 @@\n-{old}\n+{new}\n").encode()


def created(name, text):
    return (f"diff --git a/{name} b/{name}\nnew file mode 100644\n--- /dev/null\n+++ b/{name}\n"
            f"@@ -0,0 +1 @@\n+{text}\n").encode()


def git(*args):
    return subprocess.run([shutil.which("git"), *map(str, args)], check=True, capture_output=True).stdout


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def historical_selection(tmp_path):
    donor, target = tmp_path / "donor", tmp_path / "target"
    for root, revision in ((donor, migration.DONOR_SHA), (target, migration.TARGET_BASE_SHA)):
        names = git("-C", ROOT, "ls-tree", "-r", "--name-only", revision).decode().splitlines()
        for name in names:
            if name.startswith("patches/") or name in ("build/ungoogled-revisions.psd1",
                    "CHROMIUM_VERSION", "CHROMIUM_WINDOWS_VERSION"):
                put(root, name, git("-C", ROOT, "show", revision + ":" + name))
    return donor, target


def test_real_pinned_216_prefix_and_154_override(tmp_path):
    donor, target = historical_selection(tmp_path)
    selected, raw = migration.selection.select(target, "windows")
    old, prefix = migration._donor_selection(donor, target, selected, raw)
    assert len(prefix) == len(old["patches"]) == 216
    assert selected["patches"][:216] == old["patches"]
    assert raw[221][0] == migration.NEW_OVERRIDE
    assert all(migration._pins(r)["ChromiumVersion"] == migration.VERSION for r in (donor, target))
    with pytest.raises(ValueError, match="override|224"):
        migration.selection.select(donor, "windows")
    put(donor, prefix[0][0], prefix[0][1] + b"tampered\n")
    with pytest.raises(RuntimeError, match="old base patch changed"):
        migration._donor_selection(donor, target, selected, raw)


class Fixture:
    def __init__(self, tmp_path, monkeypatch):
        self.work, self.donor, self.target = (tmp_path / n for n in ("work", "donor", "target"))
        self.src = self.work / "src"
        self.core = self.work / "tooling/ungoogled-chromium"
        self.tooling = self.work / "tooling/ungoogled-chromium-windows"
        self.old_names = [f"patches/{n:04d}-fixture.patch" for n in range(1, 217)]
        self.names = self.old_names + list(migration.ADDITIONS)
        self.raw = []
        previous = "v0"
        for number, name in enumerate(self.names, 1):
            if number == 221:
                data = created("new-header.h", "noise helper")
            else:
                data = patch("source.cc", previous, f"v{number}")
                previous = f"v{number}"
            self.raw.append((name, data))
            put(self.target, name, data)
            if number <= 216:
                put(self.donor, name, data)
        for repo, names in ((self.donor, self.old_names), (self.target, self.names)):
            put(repo, "patches/series", "\n".join(names) + "\n")
            (repo / "patches/chromium154").mkdir()
            put(repo, migration.arp.LITE + "/lite.txt", "lite\n")
        put(self.core, "chromium_version.txt", migration.VERSION + "\n")
        put(self.core, "domain_regex.list", b"")
        put(self.tooling, "domain_substitution.list", b"")
        put(self.src, "source.cc", "v216\r\n")
        put(self.src, "lite.txt", "lite\n")
        put(self.src, migration.SOURCE_VERSION, "MAJOR=154\nMINOR=0\nBUILD=8037\nPATCH=97\n")
        put(self.work, ".chromix-target-arch", "arm64\n")
        for name in migration.upstream.REQUIRED:
            if not (self.src / name).exists():
                put(self.src, name, "unchanged\n")
        gn = 'target_cpu = "arm64"\ntarget_os = "win"\nchrome_pgo_phase = 0\n'
        put(self.src, "out/Default/args.gn", gn)
        for root, name in ((self.core, "flags.gn"), (self.tooling, "flags.windows.gn"),
                           (self.target, "build/args.windows.gn"), (self.target, "build/args.windows.arm64.gn")):
            put(root, name, gn)
        put(self.src, "out/Default/obj/cache.obj", b"cached object\x00")
        put(self.src, migration.upstream.MARKER, '{"immutable": true}\n')
        self.selected = {"series_sha256": migration.arp._sha((self.target / "patches/series").read_bytes()),
            "patches": [{"path": n, "sha256": migration.arp._sha(d)} for n, d in self.raw],
            "selection": {"schema_version": 1, "version": migration.VERSION, "platform": "windows",
                          "core_commit": migration.CORE, "platform_commit": migration.WINDOWS,
                          "base_series_sha256": migration.arp._sha((self.target / "patches/series").read_bytes())}}
        monkeypatch.setattr(migration, "TRUSTED_REPO", self.target)
        monkeypatch.setattr(migration, "_repository_inputs", lambda *a: ((migration.DONOR_SHA, {}, {}),
                                                                    (migration.TARGET_BASE_SHA, {}, {})))
        monkeypatch.setattr(migration.safe, "_tree", lambda git, root, pin: (pin, {}))
        monkeypatch.setattr(migration.upstream, "verify_restored", lambda *a, **kw:
            {"immutable": True, "original_args": {"assignments": migration.upstream.parse_gn_assignments(gn)}})
        monkeypatch.setattr(migration.selection, "select", lambda *a, **kw: (copy.deepcopy(self.selected), self.raw))
        current, patches, lite = migration.arp._load(self.target, self.core, self.tooling, "windows")
        old_selected, old_raw = migration._donor_selection(self.donor, self.target, self.selected, self.raw)
        previous_identity = migration._old_identity(current, old_selected)
        self.old_key = migration._key(self.donor, old_selected, old_raw)
        self.key = migration._key(self.target, self.selected, self.raw)
        values = {".chromix-source-unpacked": migration.VERSION, ".chromix-ungoogled-core": migration.CORE,
            ".chromix-ungoogled-windows": migration.WINDOWS, ".chromix-binaries-pruned": migration.CORE,
            ".chromix-domain-substituted": migration.CORE,
            ".chromix-toolchain-ready": migration.CORE + "|" + migration.WINDOWS,
            ".chromix-patches": self.old_key.rsplit("|", 1)[1],
            ".chromix-patch-selection": self.old_key.rsplit("|", 1)[1], migration.READY: self.old_key}
        for n, v in values.items():
            put(self.src, n, v + "\r\n")
        outputs = {n: migration.arp._sha((self.src / n).read_bytes())
                   for n in migration._names(patches[:216], lite)}
        put(self.src, migration.arp.MARKER, migration.arp._json({"schema_version": 1,
            "identity": previous_identity, "identity_sha256": migration.arp._sha(migration.arp._json(previous_identity)),
            "outputs": outputs}))

    def run(self):
        return migration.migrate(self.work, self.donor, self.target, migration.DONOR_SHA)


def test_full_migration_preserves_ninja_receipt_version_and_advances_markers(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    ninja = snapshot(fx.src / "out")
    immutable = {n: snapshot(fx.src)[n] for n in (migration.upstream.MARKER, migration.SOURCE_VERSION)}
    before = (fx.src / "source.cc").stat().st_mtime_ns
    report = fx.run()
    assert report["changed_files"] == ["new-header.h", "source.cc"]
    assert report["prepareSourceKey"] == report["key"] == fx.key
    assert report["old_patch_count"] == 216 and report["patch_count"] == 224
    assert (fx.src / "source.cc").read_bytes() == b"v224\r\n"
    assert (fx.src / "source.cc").stat().st_mtime_ns >= before + 1_000_000_000
    assert snapshot(fx.src / "out") == ninja
    assert all(snapshot(fx.src)[n] == value for n, value in immutable.items())
    assert (fx.src / migration.READY).read_text().strip() == fx.key
    assert (fx.src / ".chromix-patch-selection").read_text().strip() == fx.key.rsplit("|", 1)[1]
    assert not (fx.work / migration.BLOCKER).exists()
    current, patches, lite = migration.arp._load(fx.target, fx.core, fx.tooling, "windows", src=fx.src)
    assert migration.arp._completed(fx.src, current, migration._names(patches, lite))
    after = snapshot(fx.src)
    with pytest.raises(RuntimeError, match="repeat/prior"):
        fx.run()
    assert snapshot(fx.src) == after


@pytest.mark.parametrize("mutation", ["receipt", "source", "arch", "version", "blocker", "mixed", "gn"])
def test_preflight_fail_closed_without_source_writes(tmp_path, monkeypatch, mutation):
    fx = Fixture(tmp_path, monkeypatch)
    if mutation == "receipt":
        path = fx.src / migration.arp.MARKER
        data = json.loads(path.read_bytes())
        data["identity"]["selection"]["version"] = "154.0.8037.57"
        path.write_bytes(migration.arp._json(data))
    elif mutation == "source":
        put(fx.src, "source.cc", "forged\n")
    elif mutation == "arch":
        put(fx.work, ".chromix-target-arch", "x64\n")
    elif mutation == "version":
        put(fx.src, migration.SOURCE_VERSION, "MAJOR=153\nMINOR=0\nBUILD=8010\nPATCH=47\n")
    elif mutation == "blocker":
        (fx.work / migration.BLOCKER).mkdir()
    elif mutation == "mixed":
        put(fx.src, "source.cc", "v216\r\nextra\n")
    else:
        put(fx.src, "out/Default/args.gn", 'target_cpu = "arm64"\ntarget_cpu = "x64"\n')
    before = snapshot(fx.src)
    with pytest.raises((RuntimeError, ValueError)):
        fx.run()
    assert snapshot(fx.src) == before


def test_scratch_failure_leaves_blocker_and_original_source(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    before = snapshot(fx.src)
    original_apply = migration._apply

    def fail_append(stage, patches, git, reverse=False):
        if patches[0][0] == migration.ADDITIONS[0]:
            raise RuntimeError("injected append failure")
        return original_apply(stage, patches, git, reverse)

    monkeypatch.setattr(migration, "_apply", fail_append)
    with pytest.raises(RuntimeError, match="injected append"):
        fx.run()
    assert snapshot(fx.src) == before
    assert (fx.work / migration.BLOCKER / "transaction.json").is_file()
    with pytest.raises(RuntimeError, match="unfinished transaction"):
        fx.run()


def test_publish_failure_cannot_mark_target_ready(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    write = migration.safe._write

    def fail_source(path, data, **kwargs):
        if path == fx.src / "source.cc":
            raise OSError("injected publication failure")
        return write(path, data, **kwargs)

    monkeypatch.setattr(migration.safe, "_write", fail_source)
    with pytest.raises(OSError, match="publication failure"):
        fx.run()
    assert (fx.work / migration.BLOCKER).is_dir()
    assert (fx.src / migration.READY).read_text().strip() == fx.old_key
    assert not (fx.src / migration.RECEIPT).exists()


@pytest.mark.parametrize("name", ["out/Default/cache.obj", "chrome/VERSION", ".ninja_log"])
def test_patch_targets_cannot_touch_immutable_or_cache_paths(name):
    with pytest.raises(RuntimeError, match="VERSION/Ninja"):
        migration._names([("bad.patch", b"", [(name, "modify", None)])], {})


def test_wrong_sha_rejected_before_access():
    with pytest.raises(RuntimeError, match="fixed stage12 donor"):
        migration.migrate("missing", "missing", "missing", "0" * 40)


def test_pinned_repository_inputs_allow_only_fixed_preparation_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(migration.safe, "WINDOWS", True)
    donor, target = tmp_path / "old", tmp_path / "new"
    for root, revision in ((donor, migration.DONOR_SHA), (target, migration.TARGET_BASE_SHA)):
        git("clone", "--quiet", "--no-hardlinks", "--no-checkout", ROOT, root)
        git("-C", root, "checkout", "--quiet", "--detach", revision)
    migration._repository_inputs(shutil.which("git"), donor, target)
    put(target, "patches/untracked.patch", "unapproved\n")
    with pytest.raises(RuntimeError, match="extra/missing preparation"):
        migration._repository_inputs(shutil.which("git"), donor, target)


def test_forged_output_receipt_still_fails_structural_proof(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    put(fx.src, "source.cc", "not donor source\n")
    path = fx.src / migration.arp.MARKER
    saved = json.loads(path.read_bytes())
    saved["outputs"]["source.cc"] = migration.arp._sha((fx.src / "source.cc").read_bytes())
    path.write_bytes(migration.arp._json(saved))
    before = snapshot(fx.src)
    with pytest.raises(RuntimeError, match="scratch patch failed"):
        fx.run()
    assert snapshot(fx.src) == before
    assert (fx.work / migration.BLOCKER).is_dir()


def test_source_race_before_publication_preserves_blocker(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    original = migration._roundtrip

    def race(stage, patches, git_program, names):
        result = original(stage, patches, git_program, names)
        if len(patches) == 224:
            put(fx.src, "source.cc", "concurrent edit\n")
        return result

    monkeypatch.setattr(migration, "_roundtrip", race)
    with pytest.raises(RuntimeError, match="changed concurrently"):
        fx.run()
    assert (fx.src / "source.cc").read_text() == "concurrent edit\n"
    assert (fx.src / migration.READY).read_text().strip() == fx.old_key
    assert not (fx.src / "new-header.h").exists()
    assert (fx.work / migration.BLOCKER).is_dir()


def test_receipt_relocation_is_read_only(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    path = fx.src / migration.arp.MARKER
    saved = json.loads(path.read_bytes())
    expected = copy.deepcopy(saved["identity"])
    for field, suffix in (("regex", "ungoogled-chromium/domain_regex.list"),
                          ("list", "ungoogled-chromium-windows/domain_substitution.list")):
        saved["identity"][field]["path"] = "D:\\old\\work\\tooling\\" + suffix.replace("/", "\\")
    saved["identity_sha256"] = migration.arp._sha(migration.arp._json(saved["identity"]))
    path.write_bytes(migration.arp._json(saved))
    before = snapshot(fx.src)
    assert migration._completed(fx.src, expected, set(saved["outputs"])) == saved
    assert snapshot(fx.src) == before
