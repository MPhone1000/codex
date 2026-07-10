use super::*;
use pretty_assertions::assert_eq;
use tempfile::TempDir;

const TEST_AZURE_BASE_URL: &str = "https://internal.example.test/openapi";

fn read_config(codex_home: &TempDir, file_name: &str) -> anyhow::Result<String> {
    Ok(std::fs::read_to_string(codex_home.path().join(file_name))?)
}

#[test]
fn bootstrap_internal_profile_creates_profile_v2_defaults() -> anyhow::Result<()> {
    let codex_home = TempDir::new()?;

    let result = bootstrap_internal_profile(
        codex_home.path(),
        "first-ak",
        TEST_AZURE_BASE_URL,
        Some(DEFAULT_INTERNAL_PROFILE_MODEL),
    )?;
    assert_eq!(
        result.profile_path,
        codex_home.path().join(INTERNAL_PROFILE_FILE)
    );

    let global = read_toml_or_empty(&codex_home.path().join(CONFIG_TOML_FILE))?;
    let profile = read_toml_or_empty(&result.profile_path)?;
    assert_eq!(value_at_path(&global, &["profile"]), None);
    assert_eq!(value_at_path(&global, &["profiles"]), None);
    assert_eq!(
        value_at_path(&global, &["features", "prevent_idle_sleep"]).and_then(TomlValue::as_bool),
        Some(true)
    );
    assert_eq!(
        value_at_path(&profile, &["model"]).and_then(TomlValue::as_str),
        Some(DEFAULT_INTERNAL_PROFILE_MODEL)
    );
    assert_eq!(
        value_at_path(&profile, &["model_provider"]).and_then(TomlValue::as_str),
        Some(AZURE_PROVIDER_ID)
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
        Some("first-ak")
    );

    Ok(())
}

#[test]
fn bootstrap_internal_profile_preserves_existing_global_and_profile_values() -> anyhow::Result<()> {
    let codex_home = TempDir::new()?;
    std::fs::write(
        codex_home.path().join(CONFIG_TOML_FILE),
        r#"
[shell_environment_policy]
inherit = "none"

[features]
multi_agent = false
"#,
    )?;
    std::fs::write(
        codex_home.path().join(INTERNAL_PROFILE_FILE),
        r#"
model = "existing-model"

[model_providers.azure]
base_url = "https://existing.example.test/openapi"

[model_providers.azure.query_params]
ak = "existing-ak"
"#,
    )?;

    bootstrap_internal_profile(codex_home.path(), "", "", None)?;

    let global = read_toml_or_empty(&codex_home.path().join(CONFIG_TOML_FILE))?;
    let profile = read_toml_or_empty(&codex_home.path().join(INTERNAL_PROFILE_FILE))?;
    assert_eq!(
        value_at_path(&global, &["shell_environment_policy", "inherit"])
            .and_then(TomlValue::as_str),
        Some("none")
    );
    assert_eq!(
        value_at_path(&global, &["features", "multi_agent"]).and_then(TomlValue::as_bool),
        Some(false)
    );
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

#[test]
fn bootstrap_internal_profile_updates_ak_idempotently() -> anyhow::Result<()> {
    let codex_home = TempDir::new()?;

    bootstrap_internal_profile(
        codex_home.path(),
        "old-ak",
        TEST_AZURE_BASE_URL,
        Some(DEFAULT_INTERNAL_PROFILE_MODEL),
    )?;
    bootstrap_internal_profile(
        codex_home.path(),
        "new-ak",
        TEST_AZURE_BASE_URL,
        Some(DEFAULT_INTERNAL_PROFILE_MODEL),
    )?;
    let after_update = read_config(&codex_home, INTERNAL_PROFILE_FILE)?;
    assert!(after_update.contains("ak = \"new-ak\""));
    assert!(!after_update.contains("ak = \"old-ak\""));

    bootstrap_internal_profile(
        codex_home.path(),
        "new-ak",
        TEST_AZURE_BASE_URL,
        Some(DEFAULT_INTERNAL_PROFILE_MODEL),
    )?;
    let after_rerun = read_config(&codex_home, INTERNAL_PROFILE_FILE)?;
    assert_eq!(after_rerun, after_update);

    Ok(())
}

#[test]
fn bootstrap_internal_profile_uses_default_model() -> anyhow::Result<()> {
    let codex_home = TempDir::new()?;

    bootstrap_internal_profile(codex_home.path(), "ak", TEST_AZURE_BASE_URL, None)?;
    let profile = read_toml_or_empty(&codex_home.path().join(INTERNAL_PROFILE_FILE))?;
    assert_eq!(
        value_at_path(&profile, &["model"]).and_then(TomlValue::as_str),
        Some(DEFAULT_INTERNAL_PROFILE_MODEL)
    );

    Ok(())
}

#[test]
fn bootstrap_internal_profile_requires_missing_credentials() -> anyhow::Result<()> {
    let codex_home = TempDir::new()?;

    let error = bootstrap_internal_profile(codex_home.path(), "", "", None)
        .expect_err("missing credentials should fail");
    assert!(error.to_string().contains("non-empty ak"));

    Ok(())
}
