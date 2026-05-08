use anyhow::Context;
use codex_config::CONFIG_TOML_FILE;
use std::path::Path;
use std::path::PathBuf;
use toml::Value as TomlValue;
use toml_edit::Array;
use toml_edit::Item as TomlEditItem;
use toml_edit::value;

use crate::config::edit::ConfigEdit;
use crate::config::edit::ConfigEditsBuilder;

const INTERNAL_PROFILE_FILE: &str = "internal.config.toml";
pub const DEFAULT_INTERNAL_PROFILE_MODEL: &str = "gpt-5.4-2026-03-05";
const AZURE_PROVIDER_ID: &str = "azure";
const AZURE_API_VERSION: &str = "2025-04-01-preview";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BootstrapInternalProfileResult {
    pub profile_path: PathBuf,
}

pub fn bootstrap_internal_profile(
    codex_home: &Path,
    ak: &str,
    azure_base_url: &str,
    model: Option<&str>,
) -> anyhow::Result<BootstrapInternalProfileResult> {
    let config_path = codex_home.join(CONFIG_TOML_FILE);
    let profile_path = codex_home.join(INTERNAL_PROFILE_FILE);
    let existing_global = read_toml_or_empty(&config_path)?;
    let existing_profile = read_toml_or_empty(&profile_path)?;

    let ak = resolve_ak(ak, &existing_profile)?;
    let azure_base_url = resolve_azure_base_url(azure_base_url, &existing_profile)?;
    let model = resolve_model(model, &existing_profile);

    ConfigEditsBuilder::for_config_path(&profile_path)
        .with_edits(installer_owned_edits(ak, azure_base_url, model))
        .apply_blocking()?;
    ConfigEditsBuilder::new(codex_home)
        .with_edits(missing_global_default_edits(&existing_global))
        .apply_blocking()?;

    Ok(BootstrapInternalProfileResult { profile_path })
}

fn read_toml_or_empty(path: &Path) -> anyhow::Result<TomlValue> {
    let serialized = match std::fs::read_to_string(path) {
        Ok(contents) => contents,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(err) => return Err(err.into()),
    };
    if serialized.is_empty() {
        return Ok(TomlValue::Table(Default::default()));
    }

    toml::from_str::<TomlValue>(&serialized)
        .with_context(|| format!("failed to parse config at {}", path.display()))
}

fn installer_owned_edits(ak: &str, azure_base_url: &str, model: &str) -> Vec<ConfigEdit> {
    let mut edits = vec![
        set_path(&["model"], value(model)),
        set_path(&["model_provider"], value(AZURE_PROVIDER_ID)),
        set_path(&["sandbox_mode"], value("danger-full-access")),
        set_path(&["approval_policy"], value("on-request")),
        set_path(&["model_reasoning_effort"], value("xhigh")),
        set_path(&["plan_mode_reasoning_effort"], value("xhigh")),
        set_path(&["model_max_output_tokens"], value(64_000)),
        set_path(
            &["model_providers", AZURE_PROVIDER_ID, "name"],
            value("Azure"),
        ),
        set_path(
            &["model_providers", AZURE_PROVIDER_ID, "base_url"],
            value(azure_base_url),
        ),
        set_path(
            &["model_providers", AZURE_PROVIDER_ID, "wire_api"],
            value("responses"),
        ),
        set_path(
            &[
                "model_providers",
                AZURE_PROVIDER_ID,
                "query_params",
                "api-version",
            ],
            value(AZURE_API_VERSION),
        ),
        set_path(
            &["model_providers", AZURE_PROVIDER_ID, "query_params", "ak"],
            value(ak),
        ),
        set_path(
            &["model_providers", AZURE_PROVIDER_ID, "request_max_retries"],
            value(50),
        ),
        set_path(
            &["model_providers", AZURE_PROVIDER_ID, "retry_429"],
            value(true),
        ),
        set_path(
            &["model_providers", AZURE_PROVIDER_ID, "stream_max_retries"],
            value(50),
        ),
    ];

    for segments in [
        &["request_max_retries"][..],
        &["stream_max_retries"][..],
        &["model_providers", AZURE_PROVIDER_ID, "env_key"][..],
        &["model_providers", AZURE_PROVIDER_ID, "env_key_instructions"][..],
        &[
            "model_providers",
            AZURE_PROVIDER_ID,
            "experimental_bearer_token",
        ][..],
        &["model_providers", AZURE_PROVIDER_ID, "http_headers"][..],
        &["model_providers", AZURE_PROVIDER_ID, "env_http_headers"][..],
        &[
            "model_providers",
            AZURE_PROVIDER_ID,
            "stream_idle_timeout_ms",
        ][..],
        &[
            "model_providers",
            AZURE_PROVIDER_ID,
            "websocket_connect_timeout_ms",
        ][..],
        &["model_providers", AZURE_PROVIDER_ID, "requires_openai_auth"][..],
        &["model_providers", AZURE_PROVIDER_ID, "supports_websockets"][..],
    ] {
        edits.push(clear_path(segments));
    }

    edits
}

fn missing_global_default_edits(existing: &TomlValue) -> Vec<ConfigEdit> {
    let mut edits = Vec::new();

    for (segments, value) in [
        (&["shell_environment_policy", "inherit"][..], value("all")),
        (
            &["shell_environment_policy", "ignore_default_excludes"][..],
            value(true),
        ),
        (&["features", "multi_agent"][..], value(true)),
        (&["features", "prevent_idle_sleep"][..], value(true)),
        (&["background_terminal_max_timeout"][..], value(72_000_000)),
        (&["project_doc_max_bytes"][..], value(65_536)),
        (&["suppress_unstable_features_warning"][..], value(true)),
        (&["tui", "theme"][..], value("catppuccin-latte")),
        (&["tui", "notification_method"][..], value("auto")),
    ] {
        if !has_path(existing, segments) {
            edits.push(set_path(segments, value));
        }
    }

    if !has_path(existing, &["tui", "notifications"]) {
        edits.push(set_path(
            &["tui", "notifications"],
            string_array(["agent-turn-complete", "approval-requested"]),
        ));
    }

    edits
}

fn resolve_ak<'a>(
    input_ak: &'a str,
    existing: &'a TomlValue,
) -> anyhow::Result<&'a str> {
    let input_ak = input_ak.trim();
    if !input_ak.is_empty() {
        return Ok(input_ak);
    }

    if let Some(existing_ak) = value_at_path(
            existing,
            &["model_providers", AZURE_PROVIDER_ID, "query_params", "ak"],
        )
        .and_then(TomlValue::as_str)
        .map(str::trim)
        .filter(|existing_ak| !existing_ak.is_empty())
    {
        return Ok(existing_ak);
    }

    anyhow::bail!("internal installer requires a non-empty ak");
}

fn resolve_azure_base_url<'a>(
    input_azure_base_url: &'a str,
    existing: &'a TomlValue,
) -> anyhow::Result<&'a str> {
    let input_azure_base_url = input_azure_base_url.trim();
    if !input_azure_base_url.is_empty() {
        return Ok(input_azure_base_url);
    }

    if let Some(existing_azure_base_url) = value_at_path(
            existing,
            &["model_providers", AZURE_PROVIDER_ID, "base_url"],
        )
        .and_then(TomlValue::as_str)
        .map(str::trim)
        .filter(|existing_azure_base_url| !existing_azure_base_url.is_empty())
    {
        return Ok(existing_azure_base_url);
    }

    anyhow::bail!("internal installer requires a non-empty azure base URL");
}

fn resolve_model<'a>(
    input_model: Option<&'a str>,
    existing: &'a TomlValue,
) -> &'a str {
    if let Some(input_model) = input_model
        .map(str::trim)
        .filter(|input_model| !input_model.is_empty())
    {
        return input_model;
    }

    if let Some(existing_model) =
            value_at_path(existing, &["model"])
                .and_then(TomlValue::as_str)
                .map(str::trim)
                .filter(|existing_model| !existing_model.is_empty())
    {
        return existing_model;
    }

    DEFAULT_INTERNAL_PROFILE_MODEL
}

fn has_path(value: &TomlValue, segments: &[&str]) -> bool {
    value_at_path(value, segments).is_some()
}

fn value_at_path<'a>(value: &'a TomlValue, segments: &[&str]) -> Option<&'a TomlValue> {
    let mut current = value;
    for segment in segments {
        let table = current.as_table()?;
        current = table.get(*segment)?;
    }
    Some(current)
}

fn set_path(segments: &[&str], value: TomlEditItem) -> ConfigEdit {
    ConfigEdit::SetPath {
        segments: segments
            .iter()
            .map(|segment| (*segment).to_string())
            .collect(),
        value,
    }
}

fn clear_path(segments: &[&str]) -> ConfigEdit {
    ConfigEdit::ClearPath {
        segments: segments
            .iter()
            .map(|segment| (*segment).to_string())
            .collect(),
    }
}

fn string_array<const N: usize>(values: [&str; N]) -> TomlEditItem {
    let mut array = Array::new();
    for value in values {
        array.push(value);
    }
    TomlEditItem::Value(array.into())
}

#[cfg(test)]
#[path = "installer_profile_tests.rs"]
mod tests;
