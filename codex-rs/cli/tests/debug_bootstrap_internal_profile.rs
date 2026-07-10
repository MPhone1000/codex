use std::path::Path;

use anyhow::Result;
use predicates::str::contains;
use pretty_assertions::assert_eq;
use tempfile::TempDir;
use toml::Value as TomlValue;

const TEST_AZURE_BASE_URL: &str = "https://internal.example.test/openapi";
const TEST_MODEL: &str = "gpt-5.4";

fn codex_command(codex_home: &Path) -> Result<assert_cmd::Command> {
    let mut cmd = assert_cmd::Command::new(codex_utils_cargo_bin::cargo_bin("codex")?);
    cmd.env("CODEX_HOME", codex_home);
    Ok(cmd)
}

fn read_toml(path: &Path) -> Result<TomlValue> {
    Ok(toml::from_str(&std::fs::read_to_string(path)?)?)
}

fn value_at_path<'a>(value: &'a TomlValue, segments: &[&str]) -> Option<&'a TomlValue> {
    let mut current = value;
    for segment in segments {
        current = current.as_table()?.get(*segment)?;
    }
    Some(current)
}

#[tokio::test]
async fn debug_bootstrap_internal_profile_creates_profile_v2_file() -> Result<()> {
    let codex_home = TempDir::new()?;

    let mut cmd = codex_command(codex_home.path())?;
    cmd.args([
        "debug",
        "bootstrap-internal-profile",
        "--ak-stdin",
        "--azure-base-url",
        TEST_AZURE_BASE_URL,
    ])
    .write_stdin("secret-ak\n")
    .assert()
    .success()
    .stdout(contains("Run `codex --profile internal` to use it."));

    let global = read_toml(&codex_home.path().join("config.toml"))?;
    let profile = read_toml(&codex_home.path().join("internal.config.toml"))?;
    assert_eq!(value_at_path(&global, &["profile"]), None);
    assert_eq!(value_at_path(&global, &["profiles"]), None);
    assert_eq!(
        value_at_path(&profile, &["model"]).and_then(TomlValue::as_str),
        Some("gpt-5.4-2026-03-05")
    );
    assert_eq!(
        value_at_path(&profile, &["model_providers", "azure", "base_url"])
            .and_then(TomlValue::as_str),
        Some(TEST_AZURE_BASE_URL)
    );
    assert_eq!(
        value_at_path(
            &profile,
            &["model_providers", "azure", "query_params", "ak"]
        )
        .and_then(TomlValue::as_str),
        Some("secret-ak")
    );

    Ok(())
}

#[tokio::test]
async fn debug_bootstrap_internal_profile_accepts_model_override() -> Result<()> {
    let codex_home = TempDir::new()?;

    let mut cmd = codex_command(codex_home.path())?;
    cmd.args([
        "debug",
        "bootstrap-internal-profile",
        "--ak-stdin",
        "--azure-base-url",
        TEST_AZURE_BASE_URL,
        "--model",
        TEST_MODEL,
    ])
    .write_stdin("override-ak\n")
    .assert()
    .success();

    let profile = read_toml(&codex_home.path().join("internal.config.toml"))?;
    assert_eq!(
        value_at_path(&profile, &["model"]).and_then(TomlValue::as_str),
        Some(TEST_MODEL)
    );

    Ok(())
}

#[tokio::test]
async fn debug_bootstrap_internal_profile_reuses_existing_credentials() -> Result<()> {
    let codex_home = TempDir::new()?;
    std::fs::write(
        codex_home.path().join("internal.config.toml"),
        r#"
model = "existing-model"

[model_providers.azure]
base_url = "https://existing.example.test/openapi"

[model_providers.azure.query_params]
ak = "existing-ak"
"#,
    )?;

    let mut cmd = codex_command(codex_home.path())?;
    cmd.args([
        "debug",
        "bootstrap-internal-profile",
        "--ak-stdin",
        "--azure-base-url",
        "",
    ])
    .write_stdin("\n")
    .assert()
    .success();

    let profile = read_toml(&codex_home.path().join("internal.config.toml"))?;
    assert_eq!(
        value_at_path(&profile, &["model"]).and_then(TomlValue::as_str),
        Some("existing-model")
    );
    assert_eq!(
        value_at_path(
            &profile,
            &["model_providers", "azure", "query_params", "ak"]
        )
        .and_then(TomlValue::as_str),
        Some("existing-ak")
    );

    Ok(())
}
