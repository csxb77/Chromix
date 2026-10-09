"""macOS package installation remains receipt-bound and offline by default."""
import os
from pathlib import Path
import unittest
from unittest import mock

if os.name != "posix":
    raise unittest.SkipTest("macOS generator installation requires POSIX directory descriptors")

from tools import install_macos_generators as installer
from tools import macos_restored_generators as gen
from tools import prepare_restored_build as prepare
from tools import restore_upstream_cache as restore
from tools.tests import test_macos_restored_generators as fixtures


class InstallMacGeneratorsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MacGeneratorsTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.work, self.src = self.fixture.work, self.fixture.src
        self.downloads = self.work / "downloads"
        self.downloads.mkdir()
        self.fixture.write(restore.MARKER, b'{"fixture":"receipt"}')
        self.receipt = {"identity": {"fixture": "identity"}}
        self.fixture.patch(restore, "verify_restored", return_value=self.receipt)
        self.fixture.patch(prepare, "host_identity", return_value=("macos", "arm64"))
        self.network = self.fixture.patch(installer.downloads_api, "fetch_archive", side_effect=AssertionError("network forbidden"))
        for key, data in self.fixture.archives.items():
            self.archive(key).write_bytes(data)

    def archive(self, key):
        return self.downloads / gen.ARCHIVES[key]["url"].rsplit("/", 1)[-1]

    def install(self, **kwargs):
        return installer.install(self.work, "arm64", self.downloads, repo=self.work, **kwargs)

    def test_offline_install_no_execution_and_resume_without_archives(self):
        for key in ("esbuild-x64", "typescript-x64"):
            self.archive(key).unlink()
        with mock.patch.object(gen.subprocess, "run") as run:
            result = self.install()
            self.assertEqual(result["status"], "installed")
            for path in self.downloads.iterdir():
                path.unlink()
            self.assertEqual(self.install()["status"], "already_installed")
        run.assert_not_called()
        self.network.assert_not_called()
        self.assertTrue(result["generators"]["repair_needed"])

    def test_missing_cache_requires_exact_ci_flag_and_download_opt_in(self):
        self.archive("esbuild").unlink()
        for flag, download in (("true", False), ("false", True), ("True", True), ("", True)):
            with self.subTest(flag=flag, download=download), \
                    mock.patch.dict(os.environ, {"GITHUB_ACTIONS": flag}), \
                    self.assertRaisesRegex(ValueError, "offline cache"):
                self.install(download=download)
        self.network.assert_not_called()

    def test_download_only_missing_archive_validates_and_publishes_single_link_file(self):
        self.archive("esbuild").unlink()
        data = self.fixture.archives["esbuild"]
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
                mock.patch.object(installer.downloads_api, "fetch_archive", return_value=data) as fetch:
            self.assertEqual(self.install(download=True)["status"], "installed")
        fetch.assert_called_once_with(gen.ARCHIVES["esbuild"]["url"], gen.ARCHIVES["esbuild"]["sha256"])
        self.assertEqual(self.archive("esbuild").stat().st_nlink, 1)

    def test_invalid_receipt_or_host_fails_before_archive_access(self):
        with mock.patch.object(restore, "verify_restored", side_effect=restore.Miss("receipt")), \
                mock.patch.object(installer, "ensure_archive") as ensure, self.assertRaises(restore.Miss):
            self.install()
        ensure.assert_not_called()
        with mock.patch.object(prepare, "host_identity", return_value=("macos", "x64")), \
                mock.patch.object(installer, "ensure_archive") as ensure, self.assertRaisesRegex(ValueError, "native target"):
            self.install()
        ensure.assert_not_called()

    def test_receipt_change_during_download_prevents_install(self):
        original = installer.ensure_archive
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.src / restore.MARKER).write_bytes(b"changed receipt")
            return result
        with mock.patch.object(installer, "ensure_archive", side_effect=changed), \
                self.assertRaisesRegex(ValueError, "receipt changed"):
            self.install()
        self.assertFalse((self.src / gen.PACKAGES).exists())

    def test_unknown_archive_or_symlink_never_falls_back_to_download(self):
        path = self.archive("esbuild")
        path.write_bytes(b"unknown")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.install(download=True)
        path.unlink()
        path.symlink_to(self.work / "missing")
        with self.assertRaisesRegex(ValueError, "linked"):
            self.install(download=True)
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
