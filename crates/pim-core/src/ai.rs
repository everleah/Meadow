//! AI assistance via NATS request/reply (Stage 1, `plan/detail/ki-assistenz.md`).
//!
//! The core owns the **configuration** (settings registry, secret handling) and
//! the **audit trail**; the worker owns everything beyond HTTP: context building
//! from the runtime sources, the provider call, and the policy check of the
//! generated script. They talk over the same synchronous request/reply pattern
//! as `rules.compile`.
//!
//! **The API key travels only in this message** — never to the browser, never
//! into a prompt. The worker holds it for the duration of one call.

use std::sync::{Arc, RwLock};
use std::time::Duration;

use serde_json::{Map, Value, json};

use crate::config::Config;

/// Everything the worker needs for one provider call, resolved by the core.
///
/// Field names match the settings keys (`ai_*`, see `api/system.rs`) so the
/// worker can read them without a second mapping.
#[derive(Debug, Clone, PartialEq)]
pub struct AiConfig {
    pub provider: String,
    pub endpoint: String,
    pub model: String,
    pub api_key: String,
    pub temperature: f64,
    pub max_output_tokens: u64,
    pub timeout_ms: u64,
    pub max_requests_per_hour: u64,
}

impl AiConfig {
    /// Reads the AI configuration from the resolved settings snapshot.
    fn from_settings(settings: &Map<String, Value>) -> Self {
        let text = |key: &str| {
            settings
                .get(key)
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim()
                .to_owned()
        };
        Self {
            provider: {
                let provider = text("ai_provider");
                if provider.is_empty() {
                    "generic".to_owned()
                } else {
                    provider
                }
            },
            endpoint: text("ai_endpoint"),
            model: text("ai_model"),
            api_key: text("ai_api_key"),
            temperature: settings
                .get("ai_temperature")
                .and_then(Value::as_f64)
                .unwrap_or(0.2),
            max_output_tokens: settings
                .get("ai_max_output_tokens")
                .and_then(Value::as_u64)
                .unwrap_or(2048),
            timeout_ms: settings
                .get("ai_timeout_ms")
                .and_then(Value::as_u64)
                .unwrap_or(30_000),
            max_requests_per_hour: settings
                .get("ai_max_requests_per_hour")
                .and_then(Value::as_u64)
                .unwrap_or(60),
        }
    }

    /// Whether the feature is switched on (`ai_enabled`).
    fn enabled(settings: &Map<String, Value>) -> bool {
        settings
            .get("ai_enabled")
            .and_then(Value::as_bool)
            .unwrap_or(false)
    }

    /// Missing pieces a call cannot work without (empty = ready).
    ///
    /// A missing key is not an error for a local Ollama instance, so the check
    /// follows the provider: everything needs an endpoint and a model, and only
    /// the hosted providers need a key.
    fn missing(&self) -> Vec<&'static str> {
        let mut missing = Vec::new();
        if self.endpoint.is_empty() {
            missing.push("ai_endpoint");
        }
        if self.model.is_empty() {
            missing.push("ai_model");
        }
        if self.api_key.is_empty() && self.provider != "ollama" {
            missing.push("ai_api_key");
        }
        missing
    }

    /// The wire form sent to the worker (camel-case-free, same keys as the settings).
    fn to_json(&self) -> Value {
        json!({
            "provider": self.provider,
            "endpoint": self.endpoint,
            "model": self.model,
            "api_key": self.api_key,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "timeout_ms": self.timeout_ms,
            "max_requests_per_hour": self.max_requests_per_hour,
        })
    }
}

/// Failure modes of an AI request.
#[derive(Debug)]
pub enum AiError {
    /// `ai_enabled` is false — the feature is switched off on purpose.
    Disabled,
    /// Switched on, but the provider configuration is incomplete.
    NotConfigured(Vec<&'static str>),
    /// NATS connection or timeout failure.
    Transport(String),
    /// The worker replied with an unparseable payload.
    BadReply(String),
}

impl std::fmt::Display for AiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AiError::Disabled => write!(f, "AI assistance is disabled (`ai_enabled` = false)"),
            AiError::NotConfigured(missing) => write!(
                f,
                "AI provider is not configured (missing: {})",
                missing.join(", ")
            ),
            AiError::Transport(message) => write!(f, "{message}"),
            AiError::BadReply(message) => write!(f, "invalid reply: {message}"),
        }
    }
}

/// Stateless NATS client for the AI generation request.
///
/// Connection details come from `Config` (startup); the provider configuration
/// is read per call from the shared settings snapshot, so the settings page
/// takes effect without a restart.
#[derive(Debug, Clone)]
pub struct AiClient {
    nats_url: String,
    generate_subject: String,
    test_subject: String,
    settings: Arc<RwLock<Map<String, Value>>>,
}

impl AiClient {
    /// Builds the client from runtime configuration + the shared settings snapshot.
    pub fn from_config(config: &Config, settings: Arc<RwLock<Map<String, Value>>>) -> Self {
        Self {
            nats_url: config.rules_nats_url.clone(),
            generate_subject: config.ai_generate_subject.clone(),
            test_subject: config.ai_test_subject.clone(),
            settings,
        }
    }

    /// The current AI configuration (fail-closed checks included).
    fn config(&self) -> Result<(AiConfig, u64), AiError> {
        let guard = self
            .settings
            .read()
            .map_err(|_| AiError::BadReply("settings snapshot lock poisoned".to_owned()))?;
        if !AiConfig::enabled(&guard) {
            return Err(AiError::Disabled);
        }
        let config = AiConfig::from_settings(&guard);
        let timeout_ms = config.timeout_ms;
        let missing = config.missing();
        if !missing.is_empty() {
            return Err(AiError::NotConfigured(missing));
        }
        Ok((config, timeout_ms))
    }

    /// Sends a request and returns the worker's reply.
    ///
    /// The NATS timeout is the provider timeout plus a margin for context
    /// building and the policy check, so the worker's own timeout wins and the
    /// caller receives the real error message instead of a transport timeout.
    async fn request(
        &self,
        subject: &str,
        payload: Value,
        timeout_ms: u64,
    ) -> Result<Value, AiError> {
        let bytes =
            serde_json::to_vec(&payload).map_err(|error| AiError::BadReply(error.to_string()))?;
        let client = async_nats::connect(&self.nats_url)
            .await
            .map_err(|error| AiError::Transport(error.to_string()))?;
        let request = async_nats::Request::new()
            .payload(bytes.into())
            .timeout(Some(Duration::from_millis(timeout_ms + 5_000)));
        let reply = client
            .send_request(subject.to_owned(), request)
            .await
            .map_err(|error| AiError::Transport(error.to_string()))?;
        serde_json::from_slice(&reply.payload).map_err(|error| AiError::BadReply(error.to_string()))
    }

    /// Asks the worker to build a suggestion (`request` = area, mode, description, …).
    pub async fn generate(&self, request: Value) -> Result<Value, AiError> {
        let (config, timeout_ms) = self.config()?;
        let payload = json!({ "config": config.to_json(), "request": request });
        self.request(&self.generate_subject, payload, timeout_ms)
            .await
    }

    /// Asks the worker for a minimal provider round trip (the settings page's
    /// "test connection" button).
    pub async fn test(&self) -> Result<Value, AiError> {
        let (config, timeout_ms) = self.config()?;
        let payload = json!({ "config": config.to_json() });
        self.request(&self.test_subject, payload, timeout_ms).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings(pairs: &[(&str, Value)]) -> Map<String, Value> {
        let mut map = Map::new();
        map.insert("ai_enabled".to_owned(), json!(true));
        for (key, value) in pairs {
            map.insert((*key).to_owned(), value.clone());
        }
        map
    }

    #[test]
    fn a_ready_generic_provider_needs_endpoint_model_and_key() {
        let config = AiConfig::from_settings(&settings(&[
            ("ai_endpoint", json!("https://api.deepseek.com/v1")),
            ("ai_model", json!("deepseek-chat")),
            ("ai_api_key", json!("sk-x")),
        ]));
        assert_eq!(config.provider, "generic");
        assert!(config.missing().is_empty());
    }

    #[test]
    fn an_unconfigured_provider_lists_what_is_missing() {
        let config = AiConfig::from_settings(&settings(&[]));
        assert_eq!(
            config.missing(),
            vec!["ai_endpoint", "ai_model", "ai_api_key"]
        );
        // A local Ollama instance needs no key.
        let ollama = AiConfig::from_settings(&settings(&[
            ("ai_provider", json!("ollama")),
            ("ai_endpoint", json!("http://localhost:11434")),
            ("ai_model", json!("llama3.1")),
        ]));
        assert!(ollama.missing().is_empty());
        // Whitespace is not a value.
        let blank = AiConfig::from_settings(&settings(&[
            ("ai_endpoint", json!("  ")),
            ("ai_model", json!("m")),
            ("ai_api_key", json!("k")),
        ]));
        assert_eq!(blank.missing(), vec!["ai_endpoint"]);
    }

    #[test]
    fn the_defaults_match_the_settings_registry() {
        let config = AiConfig::from_settings(&settings(&[]));
        assert_eq!(config.provider, "generic");
        assert_eq!(config.temperature, 0.2);
        assert_eq!(config.max_output_tokens, 2048);
        assert_eq!(config.timeout_ms, 30_000);
        assert_eq!(config.max_requests_per_hour, 60);
        // An empty provider string falls back to `generic` instead of an empty URL.
        let empty = AiConfig::from_settings(&settings(&[("ai_provider", json!(""))]));
        assert_eq!(empty.provider, "generic");
    }

    #[test]
    fn the_api_key_travels_to_the_worker_but_not_into_the_error_messages() {
        let config = AiConfig::from_settings(&settings(&[
            ("ai_endpoint", json!("https://api.deepseek.com/v1")),
            ("ai_model", json!("deepseek-chat")),
            ("ai_api_key", json!("sk-secret")),
        ]));
        // The wire form carries the key: the worker needs it for the provider call.
        assert_eq!(config.to_json().get("api_key"), Some(&json!("sk-secret")));
        // No error text repeats it.
        let error = AiError::NotConfigured(vec!["ai_model"]).to_string();
        assert!(!error.contains("sk-secret"));
    }
}
