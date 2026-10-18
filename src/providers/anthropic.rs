//! Anthropic provider (Messages API).

use std::collections::BTreeMap;

use async_trait::async_trait;
use serde_json::{Value, json};

use super::sse::SseStream;
use super::{
    ApiError, DEFAULT_MAX_RETRIES, Provider, StreamHandler, env_base_url, http_client, prompts, send_with_retry,
};
use crate::config::HashConfig;
use crate::llm::{ChatMessage, LlmResponse, Role, ToolCall, ToolSpec};

const DEFAULT_BASE_URL: &str = "https://api.anthropic.com";
const API_VERSION: &str = "2023-06-01";

pub struct AnthropicProvider {
    config: HashConfig,
    model: String,
    api_key: String,
    base_url: String,
    client: reqwest::Client,
    pub max_retries: u32,
}

impl AnthropicProvider {
    pub fn new(config: &HashConfig) -> Self {
        let base_url = env_base_url("ANTHROPIC_BASE_URL").unwrap_or_else(|| DEFAULT_BASE_URL.to_string());
        Self::with_base_url(config, &base_url)
    }

    pub fn with_base_url(config: &HashConfig, base_url: &str) -> Self {
        AnthropicProvider {
            config: config.clone(),
            model: config.anthropic_model.clone(),
            api_key: config.anthropic_api_key.clone().unwrap_or_default(),
            base_url: base_url.trim_end_matches('/').to_string(),
            client: http_client(),
            max_retries: DEFAULT_MAX_RETRIES,
        }
    }

    fn build_request(&self, messages: &[ChatMessage], tools: Option<&[ToolSpec]>, stream: bool) -> Value {
        let (system, formatted) = format_messages(messages);
        let mut request = json!({
            "model": self.model,
            "messages": formatted,
            "max_tokens": self.config.max_response_tokens,
            "temperature": 0.7,
        });
        if let Some(system) = system.filter(|s| !s.is_empty()) {
            request["system"] = json!(system);
        }
        if let Some(tools) = tools.filter(|t| !t.is_empty()) {
            request["tools"] = Value::Array(
                tools
                    .iter()
                    .map(|t| json!({"name": t.name, "description": t.description, "input_schema": t.parameters}))
                    .collect(),
            );
        }
        if stream {
            request["stream"] = json!(true);
        }
        request
    }

    fn error_response(&self, error: &ApiError) -> LlmResponse {
        let content = match error.status {
            429 => "Anthropic rate limit exceeded. Please try again in a moment or choose a different model.".to_string(),
            401 => "Authentication failed. Please check your Anthropic API key. Run \"hi --config\" to set this up interactively."
                .to_string(),
            _ => format!("Anthropic API error: {}", with_model_hint(error.sdk_message())),
        };
        LlmResponse::text(content, &self.model)
    }

    fn parse_message(&self, message: &Value, streamed: &str) -> LlmResponse {
        let mut content = String::new();
        let mut tool_calls = Vec::new();
        for block in message.get("content").and_then(Value::as_array).into_iter().flatten() {
            match block.get("type").and_then(Value::as_str) {
                Some("text") => content.push_str(block.get("text").and_then(Value::as_str).unwrap_or("")),
                Some("tool_use") => {
                    let name = block.get("name").and_then(Value::as_str).unwrap_or("");
                    let input = block.get("input").cloned().unwrap_or_else(|| json!({}));
                    let id = block.get("id").and_then(Value::as_str).map(str::to_string);
                    tool_calls.push(ToolCall::new(name, input, id));
                }
                _ => {}
            }
        }
        if content.is_empty() && !streamed.is_empty() {
            content = streamed.to_string();
        }
        let usage = message.get("usage").filter(|u| !u.is_null()).map(|u| {
            let input = u.get("input_tokens").and_then(Value::as_i64).unwrap_or(0);
            let output = u.get("output_tokens").and_then(Value::as_i64).unwrap_or(0);
            json!({"input_tokens": input, "output_tokens": output, "total_tokens": input + output})
        });
        LlmResponse {
            content,
            tool_calls,
            model: self.model.clone(),
            usage,
        }
    }

    /// Accumulate a streamed message into the same shape as a non-streamed response.
    async fn stream_message(
        &self,
        response: reqwest::Response,
        handler: StreamHandler<'_>,
    ) -> Result<(Value, String), String> {
        let mut events = SseStream::new(response);
        let mut streamed = String::new();
        let mut blocks: BTreeMap<u64, Value> = BTreeMap::new();
        let mut partial_json: BTreeMap<u64, String> = BTreeMap::new();
        let mut usage = json!({});

        while let Some(event) = events.next_event().await {
            let event = event?;
            let Ok(data) = serde_json::from_str::<Value>(&event.data) else {
                continue;
            };
            let index = data.get("index").and_then(Value::as_u64).unwrap_or(0);
            match data.get("type").and_then(Value::as_str).unwrap_or("") {
                "message_start" => {
                    if let Some(u) = data.pointer("/message/usage") {
                        usage = u.clone();
                    }
                }
                "content_block_start" => {
                    if let Some(block) = data.get("content_block") {
                        blocks.insert(index, block.clone());
                    }
                }
                "content_block_delta" => {
                    let delta = data.get("delta").cloned().unwrap_or_default();
                    match delta.get("type").and_then(Value::as_str) {
                        Some("text_delta") => {
                            let text = delta.get("text").and_then(Value::as_str).unwrap_or("");
                            if !text.is_empty() {
                                streamed.push_str(text);
                                handler(text);
                                let block = blocks
                                    .entry(index)
                                    .or_insert_with(|| json!({"type": "text", "text": ""}));
                                let existing = block.get("text").and_then(Value::as_str).unwrap_or("").to_string();
                                block["text"] = json!(existing + text);
                            }
                        }
                        Some("input_json_delta") => {
                            let part = delta.get("partial_json").and_then(Value::as_str).unwrap_or("");
                            partial_json.entry(index).or_default().push_str(part);
                        }
                        _ => {}
                    }
                }
                "content_block_stop" => {
                    if let (Some(block), Some(raw)) = (blocks.get_mut(&index), partial_json.remove(&index)) {
                        let input = if raw.trim().is_empty() {
                            json!({})
                        } else {
                            serde_json::from_str(&raw).map_err(|e| format!("invalid tool input JSON: {e}"))?
                        };
                        block["input"] = input;
                    }
                }
                "message_delta" => {
                    if let Some(output) = data.pointer("/usage/output_tokens") {
                        usage["output_tokens"] = output.clone();
                    }
                }
                "error" => {
                    let message = data
                        .pointer("/error/message")
                        .and_then(Value::as_str)
                        .unwrap_or("stream error");
                    return Err(message.to_string());
                }
                _ => {}
            }
        }
        let content: Vec<Value> = blocks.into_values().collect();
        Ok((json!({"content": content, "usage": usage}), streamed))
    }
}

fn with_model_hint(message: String) -> String {
    if message.to_lowercase().contains("model") || message.contains("404") {
        format!("{message} Check the configured Anthropic model or run \"hi --config\".")
    } else {
        message
    }
}

/// Convert neutral messages to Anthropic's `system` + `messages`.
pub fn format_messages(messages: &[ChatMessage]) -> (Option<String>, Vec<Value>) {
    let mut system = None;
    let mut formatted = Vec::new();
    for message in messages {
        let content = message.text();
        match message.role {
            Role::System => system = Some(content.to_string()),
            Role::Assistant => {
                if !message.tool_calls.is_empty() {
                    let mut blocks = Vec::new();
                    if !content.is_empty() {
                        blocks.push(json!({"type": "text", "text": content}));
                    }
                    for tool_call in &message.tool_calls {
                        let input = match &tool_call.arguments {
                            Value::Null => json!({}),
                            other => other.clone(),
                        };
                        blocks.push(json!({
                            "type": "tool_use",
                            "id": tool_call.call_id,
                            "name": tool_call.name,
                            "input": input,
                        }));
                    }
                    formatted.push(json!({"role": "assistant", "content": blocks}));
                } else if !content.is_empty() {
                    formatted.push(json!({"role": "assistant", "content": content}));
                }
            }
            Role::User => formatted.push(json!({"role": "user", "content": content})),
            Role::Tool => match message.tool_call_id.as_deref().filter(|id| !id.is_empty()) {
                Some(id) => formatted.push(json!({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": id, "content": content}],
                })),
                None => formatted.push(json!({"role": "user", "content": format!("Tool result: {content}")})),
            },
        }
    }
    (system, formatted)
}

#[async_trait(?Send)]
impl Provider for AnthropicProvider {
    async fn generate_response(
        &self,
        messages: &[ChatMessage],
        tools: Option<&[ToolSpec]>,
        stream_handler: Option<StreamHandler<'_>>,
    ) -> LlmResponse {
        let stream = self.config.streaming && stream_handler.is_some();
        let body = self.build_request(messages, tools, stream);
        let url = format!("{}/v1/messages", self.base_url);
        let result = send_with_retry(
            || {
                self.client
                    .post(&url)
                    .header("x-api-key", &self.api_key)
                    .header("anthropic-version", API_VERSION)
                    .json(&body)
            },
            self.max_retries,
        )
        .await;
        let response = match result {
            Ok(r) => r,
            Err(e) => {
                let message = if e.is_connect() || e.is_timeout() {
                    "Connection error.".to_string()
                } else {
                    e.to_string()
                };
                return LlmResponse::text(format!("Anthropic API error: {message}"), &self.model);
            }
        };
        if !response.status().is_success() {
            return self.error_response(&ApiError::from_response(response).await);
        }
        match stream_handler.filter(|_| stream) {
            Some(handler) => match self.stream_message(response, handler).await {
                Ok((message, streamed)) => self.parse_message(&message, &streamed),
                Err(e) => LlmResponse::text(format!("Anthropic API error: {e}"), &self.model),
            },
            None => match response.json::<Value>().await {
                Ok(message) => self.parse_message(&message, ""),
                Err(e) => LlmResponse::text(format!("Unexpected error: {e}"), &self.model),
            },
        }
    }

    fn model_name(&self) -> &str {
        &self.model
    }

    fn system_prompt(&self) -> String {
        prompts::anthropic(&self.config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_messages_and_tools() {
        let messages = vec![
            ChatMessage::system("SYS"),
            ChatMessage::user("list files"),
            ChatMessage::assistant_tool_calls(
                Some("Running ls".into()),
                vec![ToolCall::new(
                    "execute_shell_command",
                    json!({"command": "ls"}),
                    Some("tu_1".into()),
                )],
            ),
            ChatMessage::tool_result("tu_1", "a.txt"),
            ChatMessage::assistant(""),
        ];
        let (system, formatted) = format_messages(&messages);
        assert_eq!(system.as_deref(), Some("SYS"));
        assert_eq!(formatted.len(), 3);
        assert_eq!(
            formatted[1]["content"][0],
            json!({"type": "text", "text": "Running ls"})
        );
        assert_eq!(
            formatted[1]["content"][1],
            json!({"type": "tool_use", "id": "tu_1", "name": "execute_shell_command", "input": {"command": "ls"}})
        );
        assert_eq!(
            formatted[2],
            json!({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "a.txt"}]})
        );

        let config = HashConfig::default();
        let provider = AnthropicProvider::with_base_url(&config, "http://localhost");
        let tools = crate::llm::available_tools(&config, None);
        let request = provider.build_request(&messages, Some(&tools), true);
        assert_eq!(request["system"], "SYS");
        assert_eq!(request["temperature"], 0.7);
        assert_eq!(request["stream"], true);
        assert_eq!(request["tools"][1]["name"], "execute_shell_command");
        assert!(request["tools"][1].get("input_schema").is_some());
    }

    #[test]
    fn parses_message_blocks() {
        let provider = AnthropicProvider::with_base_url(&HashConfig::default(), "http://localhost");
        let message = json!({
            "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "tu_9", "name": "lookup_tldr_command", "input": {"command": "tar"}}
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5}
        });
        let parsed = provider.parse_message(&message, "");
        assert_eq!(parsed.content, "Let me check.");
        assert_eq!(parsed.tool_calls[0].call_id, "tu_9");
        assert_eq!(parsed.usage.unwrap()["total_tokens"], 15);
    }

    #[test]
    fn maps_errors() {
        let provider = AnthropicProvider::with_base_url(&HashConfig::default(), "http://localhost");
        assert!(
            provider
                .error_response(&ApiError {
                    status: 401,
                    body: String::new()
                })
                .content
                .contains("Anthropic API key")
        );
        let other = provider.error_response(&ApiError {
            status: 400,
            body: "{\"type\":\"error\",\"error\":{\"type\":\"invalid_request_error\",\"message\":\"bad\"}}".into(),
        });
        assert!(other.content.starts_with("Anthropic API error: Error code: 400 - "));
    }
}
