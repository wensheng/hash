//! LLM providers implemented over their HTTP APIs.
//!
//! Providers never fail: API and transport errors are returned as response text,
//! matching the Python implementation.

pub mod anthropic;
pub mod google;
pub mod openai;
pub mod prompts;
pub mod sse;

use std::time::Duration;

use async_trait::async_trait;

use crate::config::{HashConfig, LlmProvider};
use crate::llm::{ChatMessage, LlmResponse, ToolSpec};

pub use anthropic::AnthropicProvider;
pub use google::GoogleProvider;
pub use openai::OpenAiProvider;

/// Callback receiving streamed text deltas.
pub type StreamHandler<'a> = &'a mut dyn FnMut(&str);

/// Reborrow an optional stream handler for a single call.
pub fn reborrow<'a>(handler: &'a mut Option<StreamHandler<'_>>) -> Option<StreamHandler<'a>> {
    match handler {
        Some(h) => Some(&mut **h),
        None => None,
    }
}

#[async_trait(?Send)]
pub trait Provider {
    async fn generate_response(
        &self,
        messages: &[ChatMessage],
        tools: Option<&[ToolSpec]>,
        stream_handler: Option<StreamHandler<'_>>,
    ) -> LlmResponse;

    fn model_name(&self) -> &str;

    fn system_prompt(&self) -> String;
}

pub fn create_provider(config: &HashConfig) -> Box<dyn Provider> {
    match config.llm_provider {
        LlmProvider::Openai => Box::new(OpenAiProvider::new(config)),
        LlmProvider::Anthropic => Box::new(AnthropicProvider::new(config)),
        LlmProvider::Google => Box::new(GoogleProvider::new(config)),
    }
}

pub(crate) const DEFAULT_MAX_RETRIES: u32 = 2;

/// Select the TLS crypto backend once per process (reqwest is built without a default).
pub(crate) fn install_crypto_provider() {
    static INSTALL: std::sync::Once = std::sync::Once::new();
    INSTALL.call_once(|| {
        let _ = rustls::crypto::ring::default_provider().install_default();
    });
}

pub(crate) fn http_client() -> reqwest::Client {
    install_crypto_provider();
    reqwest::Client::builder()
        .user_agent(concat!("hashcli/", env!("CARGO_PKG_VERSION")))
        .connect_timeout(Duration::from_secs(15))
        .read_timeout(Duration::from_secs(300))
        .build()
        .unwrap_or_default()
}

pub(crate) fn env_base_url(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .map(|v| v.trim().trim_end_matches('/').to_string())
        .filter(|v| !v.is_empty())
}

fn is_retryable_status(status: reqwest::StatusCode) -> bool {
    matches!(status.as_u16(), 408 | 409 | 429) || status.is_server_error()
}

fn retry_delay(response: Option<&reqwest::Response>, attempt: u32) -> Duration {
    let header = response
        .and_then(|r| r.headers().get(reqwest::header::RETRY_AFTER))
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.trim().parse::<f64>().ok())
        .filter(|secs| *secs >= 0.0 && *secs <= 60.0);
    match header {
        Some(secs) => Duration::from_secs_f64(secs),
        None => Duration::from_millis((500u64 << attempt).min(8_000)),
    }
}

/// Send a request, retrying transient failures (like the official SDKs do).
pub(crate) async fn send_with_retry(
    make_request: impl Fn() -> reqwest::RequestBuilder,
    max_retries: u32,
) -> Result<reqwest::Response, reqwest::Error> {
    let mut attempt = 0;
    loop {
        let result = make_request().send().await;
        let retry = match &result {
            Ok(response) => is_retryable_status(response.status()),
            Err(e) => e.is_connect() || e.is_timeout(),
        };
        if !retry || attempt >= max_retries {
            return result;
        }
        tokio::time::sleep(retry_delay(result.as_ref().ok(), attempt)).await;
        attempt += 1;
    }
}

/// A non-success HTTP response, read in full.
pub(crate) struct ApiError {
    pub status: u16,
    pub body: String,
}

impl ApiError {
    pub async fn from_response(response: reqwest::Response) -> Self {
        let status = response.status().as_u16();
        let body = response.text().await.unwrap_or_default();
        ApiError { status, body }
    }

    /// Response body on one line (compact JSON when possible).
    pub fn compact_body(&self) -> String {
        serde_json::from_str::<serde_json::Value>(&self.body)
            .map(|v| v.to_string())
            .unwrap_or_else(|_| self.body.trim().to_string())
    }

    /// Like the Python SDKs: `Error code: 404 - {...}`.
    pub fn sdk_message(&self) -> String {
        format!("Error code: {} - {}", self.status, self.compact_body())
    }
}
