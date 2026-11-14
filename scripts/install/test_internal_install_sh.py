#!/usr/bin/env python3

"""Offline regression tests for the fork's packaged CLI installer."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from typing import Optional
import unittest


INSTALL_SCRIPT = Path(__file__).with_name("install.sh")
VERSION = "0.158.0"
MAC_TARGET = "aarch64-apple-darwin"
LINUX_TARGET = "x86_64-unknown-linux-musl"


def write_executable(path: Path, output: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n", encoding="utf-8")
    path.chmod(0o755)


def create_release(
    root: Path,
    target: str,
    *,
    version: str = VERSION,
    include_bwrap: bool = True,
    manifest_target: Optional[str] = None,
    corrupt_checksum: bool = False,
) -> None:
    package = root / f"package-{version}"
    package.mkdir()
    write_executable(package / "bin/codex", f"codex {version}")
    write_executable(package / "bin/codex-code-mode-host", "host")
    write_executable(package / "codex-path/rg", "rg")
    if "linux" in target and include_bwrap:
        write_executable(package / "codex-resources/bwrap", "bwrap")
    else:
        (package / "codex-resources").mkdir()
    (package / "codex-package.json").write_text(
        json.dumps(
            {
                "layoutVersion": 1,
                "version": version,
                "target": manifest_target or target,
                "variant": "codex",
                "entrypoint": "bin/codex",
                "resourcesDir": "codex-resources",
                "pathDir": "codex-path",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    release = root / "releases" / f"internal-rust-v{version}"
    release.mkdir(parents=True)
    asset_name = f"codex-package-{target}.tar.gz"
    archive_path = release / asset_name
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(package.rglob("*")):
            archive.add(path, arcname=path.relative_to(package), recursive=False)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if corrupt_checksum:
        digest = "0" * 64
    (release / f"{asset_name}.sha256").write_text(
        f"{digest}  {asset_name}\n", encoding="utf-8"
    )


def run_installer(
    root: Path, target: str, *, version: str = VERSION
) -> subprocess.CompletedProcess[str]:
    system = "Linux" if "linux" in target else "Darwin"
    machine = "aarch64" if target.startswith("aarch64") else "x86_64"
    home = root / "home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(root / "codex-home"),
            "CODEX_INSTALL_DIR": str(root / "install-bin"),
            "CODEX_INSTALL_RELEASE_BASE_URL": (root / "releases").as_uri(),
            "CODEX_INSTALL_UNAME_S": system,
            "CODEX_INSTALL_UNAME_M": machine,
            "CODEX_INSTALL_PROC_TRANSLATED": "0",
            "CODEX_INSTALL_AK": "test-key",
            "CODEX_INSTALL_AZURE_BASE_URL": "https://example.invalid/openai",
        }
    )
    return subprocess.run(
        ["sh", str(INSTALL_SCRIPT), version],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class InternalInstallShTest(unittest.TestCase):
    def test_upgrades_flat_install_to_complete_macos_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_cli = root / "install-bin/codex"
            write_executable(old_cli, "old cli")
            create_release(root, MAC_TARGET)

            result = run_installer(root, MAC_TARGET)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(old_cli.is_symlink())
            entrypoint = old_cli.resolve()
            releases_dir = (root / "codex-home/packages/internal/releases").resolve()
            self.assertEqual(
                entrypoint.relative_to(releases_dir).parts[-2:], ("bin", "codex")
            )
            self.assertTrue((entrypoint.parent.parent / "codex-package.json").is_file())
            self.assertTrue((entrypoint.parent / "codex-code-mode-host").is_file())
            self.assertTrue((entrypoint.parent.parent / "codex-path/rg").is_file())
            self.assertEqual(
                subprocess.check_output([old_cli, "--version"], text=True).strip(),
                f"codex {VERSION}",
            )

    def test_linux_package_includes_bwrap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            create_release(root, LINUX_TARGET)

            result = run_installer(root, LINUX_TARGET)

            self.assertEqual(result.returncode, 0, result.stderr)
            package = (root / "install-bin/codex").resolve().parent.parent
            self.assertTrue(os.access(package / "codex-resources/bwrap", os.X_OK))

    def test_update_switches_to_new_package_and_preserves_previous_release(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            create_release(root, MAC_TARGET)
            first = run_installer(root, MAC_TARGET)
            self.assertEqual(first.returncode, 0, first.stderr)
            visible_cli = root / "install-bin/codex"
            previous = visible_cli.resolve()

            newer_version = "0.158.1"
            create_release(root, MAC_TARGET, version=newer_version)
            updated = run_installer(root, MAC_TARGET, version=newer_version)

            self.assertEqual(updated.returncode, 0, updated.stderr)
            self.assertNotEqual(visible_cli.resolve(), previous)
            self.assertTrue(previous.is_file())
            self.assertEqual(
                subprocess.check_output([visible_cli, "--version"], text=True).strip(),
                f"codex {newer_version}",
            )

    def test_bad_checksum_keeps_existing_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_cli = root / "install-bin/codex"
            write_executable(old_cli, "old cli")
            create_release(root, MAC_TARGET, corrupt_checksum=True)

            result = run_installer(root, MAC_TARGET)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Checksum mismatch", result.stderr)
            self.assertFalse(old_cli.is_symlink())
            self.assertEqual(
                old_cli.read_text(encoding="utf-8"),
                "#!/bin/sh\nprintf '%s\\n' 'old cli'\n",
            )

    def test_incomplete_linux_package_keeps_existing_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_cli = root / "install-bin/codex"
            write_executable(old_cli, "old cli")
            create_release(root, LINUX_TARGET, include_bwrap=False)

            result = run_installer(root, LINUX_TARGET)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("codex-resources/bwrap", result.stderr)
            self.assertFalse(old_cli.is_symlink())
            releases = root / "codex-home/packages/internal/releases"
            self.assertEqual(list(releases.iterdir()), [])

    def test_wrong_target_keeps_existing_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_cli = root / "install-bin/codex"
            write_executable(old_cli, "old cli")
            create_release(root, MAC_TARGET, manifest_target=LINUX_TARGET)

            result = run_installer(root, MAC_TARGET)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match", result.stderr)
            self.assertFalse(old_cli.is_symlink())


if __name__ == "__main__":
    unittest.main()
