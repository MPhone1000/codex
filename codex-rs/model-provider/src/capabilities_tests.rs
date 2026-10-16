use super::*;
use crate::create_model_provider;
use codex_model_provider_info::ModelProviderCapabilities;
use pretty_assertions::assert_eq;

#[test]
fn capability_overrides_preserve_unspecified_provider_defaults() {
    for info in [
        ModelProviderInfo::default(),
        ModelProviderInfo::create_openai_provider(/*base_url*/ None),
        ModelProviderInfo {
            name: "Azure".to_string(),
            ..Default::default()
        },
    ] {
        let defaults = create_model_provider(info.clone(), /*auth_manager*/ None).capabilities();
        let provider = create_model_provider(
            ModelProviderInfo {
                capabilities: Some(ModelProviderCapabilities {
                    external_web_access: Some(false),
                    ..Default::default()
                }),
                ..info
            },
            /*auth_manager*/ None,
        );
        assert_eq!(
            provider.capabilities(),
            ProviderCapabilities {
                external_web_access: false,
                ..defaults
            },
        );
    }
}

#[test]
fn azure_remote_compaction_requires_explicit_capability_override() {
    let info = ModelProviderInfo {
        name: "Azure".to_string(),
        base_url: Some("https://example.openai.azure.com/openai/v1".to_string()),
        ..Default::default()
    };
    let defaults = create_model_provider(info.clone(), /*auth_manager*/ None).capabilities();
    assert_eq!(defaults, ProviderCapabilities::default());

    let provider = create_model_provider(
        ModelProviderInfo {
            capabilities: Some(ModelProviderCapabilities {
                remote_compaction: Some(RemoteCompactionSupport::V2),
                ..Default::default()
            }),
            ..info
        },
        /*auth_manager*/ None,
    );
    assert_eq!(
        provider.capabilities(),
        ProviderCapabilities {
            remote_compaction: RemoteCompactionSupport::V2,
            ..defaults
        },
    );
}
