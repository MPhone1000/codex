#!/usr/bin/env python3

"""Offline regression tests for the fork's packaged CLI installer."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import tomllib
from typing import Optional
import unittest

from build_ark_model_templates import build_templates


INSTALL_SCRIPT = Path(__file__).with_name("install.sh")
VERSION = "0.158.0"
MAC_TARGET = "aarch64-apple-darwin"
LINUX_TARGET = "x86_64-unknown-linux-musl"
ARK_MODEL = "ep-20260101000000-test"
ARK_MODEL_TEMPLATE = "gpt-6.2-astra"
ARK_BASE_URL = "https://ark.example.invalid/api/v3"
ARK_TEST_KEY = "test-ark-key"


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
    root: Path,
    target: str,
    *,
    version: str = VERSION,
    ark: bool = False,
    extra_env: Optional[dict[str, str]] = None,
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
    args = [version]
    if ark:
        args.insert(0, "--ark")
        env.pop("CODEX_INSTALL_AK")
        env.pop("CODEX_INSTALL_AZURE_BASE_URL")
        env.pop("CODEX_INSTALL_ARK_MODEL_TEMPLATE", None)
        env.update(
            ARK_API_KEY=ARK_TEST_KEY,
            CODEX_INSTALL_ARK_MODEL=ARK_MODEL,
            CODEX_INSTALL_ARK_BASE_URL=ARK_BASE_URL,
        )
    env.update(extra_env or {})
    return subprocess.run(
        ["sh", str(INSTALL_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


MODELS_PATH = INSTALL_SCRIPT.resolve().parents[2] / "codex-rs/models-manager/models.json"


def expected_ark_model(slug: str) -> dict:
    catalog = json.loads(MODELS_PATH.read_text(encoding="utf-8"))
    (model,) = (m for m in catalog["models"] if m["slug"] == slug)
    return {**model, "display_name": slug, "slug": ARK_MODEL}


def create_ark_templates(root: Path, *, corrupt_checksum: bool = False) -> None:
    release = root / "releases" / f"internal-rust-v{VERSION}"
    asset = release / "ark-model-templates.tar.gz"
    build_templates(MODELS_PATH, asset)
    digest = (
        "0" * 64 if corrupt_checksum else hashlib.sha256(asset.read_bytes()).hexdigest()
    )
    asset.with_name(f"{asset.name}.sha256").write_text(
        f"{digest}  {asset.name}\n", encoding="utf-8"
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
            config = tomllib.loads((root / "codex-home/config.toml").read_text())
            self.assertEqual(config["model_provider"], "azure")
            self.assertEqual(
                config["model_providers"]["azure"]["query_params"]["ak"], "test-key"
            )
            self.assertFalse((root / "codex-home/ark.models.json").exists())

    def test_ark_replaces_main_config_and_preserves_previous_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            create_release(root, MAC_TARGET)
            create_ark_templates(root)
            codex_home = root / "codex-home"
            codex_home.mkdir()
            config_path = codex_home / "config.toml"
            models_path = codex_home / "ark.models.json"
            previous_config = 'model_provider = "azure"\n'
            previous_catalog = '{"models": []}\n'
            config_path.write_text(previous_config)
            models_path.write_text(previous_catalog)

            result = run_installer(
                root,
                MAC_TARGET,
                ark=True,
                extra_env={"CODEX_INSTALL_ARK_MODEL_TEMPLATE": ARK_MODEL_TEMPLATE},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "install-bin/codex").is_symlink())
            config_text = config_path.read_text()
            config = tomllib.loads(config_text)
            self.assertEqual(config["model_provider"], "ark")
            self.assertEqual(config["model"], ARK_MODEL)
            self.assertEqual(Path(config["model_catalog_json"]), models_path.resolve())
            self.assertEqual(config["model_reasoning_effort"], "xhigh")
            self.assertEqual(config["model_max_output_tokens"], 64000)
            self.assertTrue(config["shell_environment_policy"]["ignore_default_excludes"])
            (provider,) = config["model_providers"].values()
            self.assertEqual(provider["experimental_bearer_token"], ARK_TEST_KEY)
            self.assertNotIn("env_key", provider)
            self.assertEqual(provider["base_url"], ARK_BASE_URL)
            self.assertEqual(provider["wire_api"], "responses")
            self.assertFalse(provider["supports_websockets"])
            self.assertNotIn("query_params", provider)
            self.assertNotIn(ARK_TEST_KEY, result.stdout + result.stderr)
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
            self.assertFalse((codex_home / "ark.config.toml").exists())
            self.assertEqual(
                json.loads(models_path.read_text()),
                {"models": [expected_ark_model(ARK_MODEL_TEMPLATE)]},
            )
            (backup,) = codex_home.glob("ark-install-backup.*")
            self.assertEqual((backup / config_path.name).read_text(), previous_config)
            self.assertEqual((backup / models_path.name).read_text(), previous_catalog)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o700)

    def test_ark_rejects_invalid_inputs_before_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for env, message in (
                ({"ARK_API_KEY": ""}, "Export ARK_API_KEY"),
                ({"ARK_API_KEY": "Bearer test-key"}, "raw API key"),
                ({"ARK_API_KEY": "test-key\nextra"}, "raw API key"),
                ({"CODEX_INSTALL_ARK_MODEL": "gpt-6.2-astra"}, "endpoint ID"),
                (
                    {"CODEX_INSTALL_ARK_BASE_URL": ARK_BASE_URL + "/responses/"},
                    "without /responses",
                ),
            ):
                with self.subTest(env=env):
                    result = run_installer(root, MAC_TARGET, ark=True, extra_env=env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)
                    self.assertFalse((root / "codex-home").exists())

    def test_ark_escapes_api_key_in_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            create_release(root, MAC_TARGET)
            key = 'test"quote\\backslash'

            result = run_installer(
                root, MAC_TARGET, ark=True, extra_env={"ARK_API_KEY": key}
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            config = tomllib.loads((root / "codex-home/config.toml").read_text())
            self.assertEqual(
                config["model_providers"]["ark"]["experimental_bearer_token"], key
            )

    def test_ark_without_template_does_not_download_or_configure_a_catalog(self) -> None:
        for existing_catalog in [False, True]:
            with self.subTest(existing_catalog=existing_catalog):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    create_release(root, MAC_TARGET)
                    codex_home = root / "codex-home"
                    codex_home.mkdir()
                    config_path = codex_home / "config.toml"
                    models_path = codex_home / "ark.models.json"
                    config_path.write_text('model_provider = "azure"\n')
                    if existing_catalog:
                        models_path.write_text('{"models": []}\n')

                    result = run_installer(root, MAC_TARGET, ark=True)

                    self.assertEqual(result.returncode, 0, result.stderr)
                    config = tomllib.loads(config_path.read_text())
                    self.assertEqual(config["model_provider"], "ark")
                    self.assertEqual(config["model"], ARK_MODEL)
                    self.assertNotIn("model_catalog_json", config)
                    (backup,) = codex_home.glob("ark-install-backup.*")
                    self.assertTrue((backup / "config.toml").is_file())
                    self.assertFalse((backup / "ark.models.json").exists())
                    self.assertEqual(models_path.exists(), existing_catalog)
                    if existing_catalog:
                        self.assertEqual(models_path.read_text(), '{"models": []}\n')

    def test_ark_uses_requested_model_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            create_release(root, LINUX_TARGET)
            create_ark_templates(root)
            slug = "gpt-6.2-sol"

            result = run_installer(
                root,
                LINUX_TARGET,
                ark=True,
                extra_env={"CODEX_INSTALL_ARK_MODEL_TEMPLATE": slug},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            catalog = json.loads((root / "codex-home/ark.models.json").read_text())
            self.assertEqual(catalog, {"models": [expected_ark_model(slug)]})

    def test_ark_unknown_template_does_not_silently_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            create_release(root, MAC_TARGET)
            create_ark_templates(root)

            result = run_installer(
                root,
                MAC_TARGET,
                ark=True,
                extra_env={"CODEX_INSTALL_ARK_MODEL_TEMPLATE": "not-a-model"},
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Ark model template 'not-a-model' is missing", result.stderr)
            self.assertFalse((root / "codex-home/config.toml").exists())
            self.assertFalse((root / "install-bin/codex").exists())

    def test_ark_bad_template_checksum_leaves_existing_install_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            create_release(root, MAC_TARGET)
            first = run_installer(root, MAC_TARGET)
            self.assertEqual(first.returncode, 0, first.stderr)
            config = root / "codex-home/config.toml"
            original_config = config.read_bytes()
            cli = root / "install-bin/codex"
            original_cli = cli.resolve()
            create_ark_templates(root, corrupt_checksum=True)

            result = run_installer(
                root,
                MAC_TARGET,
                ark=True,
                extra_env={"CODEX_INSTALL_ARK_MODEL_TEMPLATE": ARK_MODEL_TEMPLATE},
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Checksum mismatch for ark-model-templates.tar.gz", result.stderr)
            self.assertEqual(cli.resolve(), original_cli)
            self.assertEqual(config.read_bytes(), original_config)
            self.assertFalse((root / "codex-home/ark.models.json").exists())

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
