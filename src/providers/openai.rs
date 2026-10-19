//! OpenAI provider (Responses API).

use async_trait::async_trait;
use serde_json::{Value, json};

use super::sse::SseStream;
use super::{
    ApiError, DEFAULT_MAX_RETRIES, Provider, StreamHandler, env_base_url, http_client, prompts, send_with_retry,
};
use crate::config::HashConfig;
use crate::llm::{ChatMessage, LlmResponse, Role, ToolCall, ToolSpec};

const DEFAULT_BASE_URL: &str = "https://api.openai.com/v1";

pub struct OpenAiProvider {
    config: HashConfig,
    model: String,
    api_key: String,
    base_url: String,
    client: reqwest::Client,
    pub max_retries: u32,
}

impl OpenAiProvider {
    pub fn new(config: &HashConfig) -> Self {
        let base_url = config
            .openai_base_url
            .as_deref()
            .map(|u| u.trim().trim_end_matches('/').to_string())
            .filter(|u| !u.is_empty())
            .or_else(|| env_base_url("OPENAI_BASE_URL"))
            .unwrap_or_else(|| DEFAULT_BASE_URL.to_string());
        Self::with_base_url(config, &base_url)
    }

    pub fn with_base_url(config: &HashConfig, base_url: &str) -> Self {
        OpenAiProvider {
            config: config.clone(),
            model: config.openai_model.clone(),
            api_key: config.openai_api_key.clone().unwrap_or_default(),
            base_url: base_url.trim_end_matches('/').to_string(),
            client: http_client(),
            max_retries: DEFAULT_MAX_RETRIES,
        }
    }

    fn build_request(&self, messages: &[ChatMessage], tools: Option<&[ToolSpec]>, stream: bool) -> Value {
        let mut instructions = Vec::new();
        let mut input_messages = Vec::new();
        for message in messages {
            if message.role == Role::System {
                if !message.text().is_empty() {
                    instructions.push(message.text().to_string());
                }
            } else {
                input_messages.push(message.clone());
            }
        }
        let source = if input_messages.is_empty() {
            messages.to_vec()
        } else {
            input_messages
        };

        let mut text = json!({"format": {"type": "text"}});
        if let Some(verbosity) = self.config.openai_text_verbosity.as_deref().filter(|v| !v.is_empty()) {
            text["verbosity"] = json!(verbosity);
        }
        let mut request = json!({
            "model": self.model,
            "input": format_messages_for_responses(&source),
            "max_output_tokens": self.config.max_response_tokens,
            "text": text,
        });
        if let Some(tools) = tools.filter(|t| !t.is_empty()) {
            request["tools"] = Value::Array(
                tools
                    .iter()
                    .map(|t| {
                        json!({
                            "type": "function",
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                            "strict": true,
                        })
                    })
                    .collect(),
            );
            request["tool_choice"] = json!("auto");
        }
        if !instructions.is_empty() {
            request["instructions"] = json!(instructions.join("\n\n"));
        }
        if let Some(effort) = self.config.openai_reasoning_effort.as_deref().filter(|e| !e.is_empty()) {
            request["reasoning"] = json!({"effort": effort});
        }
        if stream {
            request["stream"] = json!(true);
        }
        request
    }

    fn error_response(&self, error: &ApiError) -> LlmResponse {
        let content = match error.status {
            429 => "OpenAI rate limit exceeded. Please try again in a moment or choose a different model.".to_string(),
            401 => "Authentication failed. Please check your OpenAI API key. Run \"hi --config\" to set this up interactively."
                .to_string(),
            _ => format!("OpenAI API error: {}", with_model_hint(error.sdk_message())),
        };
        LlmResponse::text(content, &self.model)
    }

    async fn stream_response(
        &self,
        response: reqwest::Response,
        handler: StreamHandler<'_>,
    ) -> Result<(Value, String), String> {
        let mut events = SseStream::new(response);
        let mut streamed = String::new();
        let mut final_response = None;
        while let Some(event) = events.next_event().await {
            let event = event?;
            let Ok(data) = serde_json::from_str::<Value>(&event.data) else {
                continue;
            };
            match data.get("type").and_then(Value::as_str).unwrap_or("") {
                "response.output_text.delta" | "response.refusal.delta" => {
                    if let Some(delta) = data.get("delta").and_then(Value::as_str).filter(|d| !d.is_empty()) {
                        streamed.push_str(delta);
                        handler(delta);
                    }
                }
                "response.completed" | "response.incomplete" | "response.failed" => {
                    final_response = data.get("response").cloned();
                }
                "error" => {
                    let message = data
                        .get("message")
                        .or_else(|| data.pointer("/error/message"))
                        .and_then(Value::as_str)
                        .unwrap_or("stream error");
                    return Err(message.to_string());
                }
                _ => {}
            }
        }
        Ok((final_response.unwrap_or_else(|| json!({"output": []})), streamed))
    }

    fn parse_response(&self, response: &Value, streamed: &str) -> LlmResponse {
        if let Some(error) = response.get("error").filter(|e| !e.is_null()) {
            let code = error.get("code").and_then(Value::as_str).unwrap_or("unknown_error");
            let message = error
                .get("message")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| error.to_string());
            return LlmResponse::text(format!("OpenAI response error ({code}): {message}"), &self.model);
        }

        let mut content_parts: Vec<String> = Vec::new();
        let mut tool_calls = Vec::new();
        let mut reasoning_summaries = Vec::new();

        for output in response.get("output").and_then(Value::as_array).into_iter().flatten() {
            match output.get("type").and_then(Value::as_str).unwrap_or("") {
                "message" => match output.get("content") {
                    Some(Value::String(text)) if !text.is_empty() => content_parts.push(text.clone()),
                    Some(Value::Array(items)) => {
                        content_parts.extend(items.iter().filter_map(extract_text_from_content));
                    }
                    _ => {}
                },
                "output_text" | "text" => {
                    if let Some(text) = output.get("text").and_then(Value::as_str).filter(|t| !t.is_empty()) {
                        content_parts.push(text.to_string());
                    }
                }
                "function_call" => {
                    let (Some(name), Some(raw)) = (output.get("name").and_then(Value::as_str), output.get("arguments"))
                    else {
                        continue;
                    };
                    let arguments = match raw {
                        Value::String(s) => match serde_json::from_str::<Value>(s) {
                            Ok(v) => v,
                            Err(_) => {
                                content_parts.push(format!("\n\nNote: Malformed tool call arguments: {s}"));
                                continue;
                            }
                        },
                        Value::Null => continue,
                        other => other.clone(),
                    };
                    tool_calls.push(ToolCall::new(name, arguments, call_id(output)));
                }
                "custom_tool_call" => {
                    let (Some(name), Some(raw)) = (output.get("name").and_then(Value::as_str), output.get("input"))
                    else {
                        continue;
                    };
                    let arguments = match raw {
                        Value::String(s) => serde_json::from_str(s).unwrap_or_else(|_| json!({"input": s})),
                        Value::Null => continue,
                        other => other.clone(),
                    };
                    tool_calls.push(ToolCall::new(name, arguments, call_id(output)));
                }
                "reasoning" => {
                    for summary in output.get("summary").and_then(Value::as_array).into_iter().flatten() {
                        if let Some(text) = summary.get("text").and_then(Value::as_str).filter(|t| !t.is_empty()) {
                            reasoning_summaries.push(text.to_string());
                        }
                    }
                }
                _ => {}
            }
        }

        let mut content = content_parts.concat();
        if content.is_empty() && !streamed.is_empty() {
            content = streamed.to_string();
        }
        if content.is_empty()
            && let Some(text) = response.get("output_text").and_then(Value::as_str)
        {
            content = text.to_string();
        }
        if content.is_empty() && tool_calls.is_empty() && !reasoning_summaries.is_empty() {
            content = format!(
                "Reasoning summary (no final text output): {}",
                reasoning_summaries.join(" ")
            );
        }
        if content.is_empty() && tool_calls.is_empty() {
            let mut detail = String::new();
            if let Some(status) = response
                .get("status")
                .and_then(Value::as_str)
                .filter(|s| *s != "completed")
            {
                detail = format!(" (status: {status})");
            }
            if let Some(incomplete) = response.get("incomplete_details").filter(|v| !v.is_null()) {
                detail = format!("{detail} (incomplete: {incomplete})");
            }
            content =
                format!("No response generated by OpenAI.{detail} Try another model or run with --debug for details.");
        }

        let usage = response.get("usage").filter(|u| !u.is_null()).map(|u| {
            json!({
                "prompt_tokens": u.get("input_tokens"),
                "completion_tokens": u.get("output_tokens"),
                "total_tokens": u.get("total_tokens"),
            })
        });

        LlmResponse {
            content,
            tool_calls,
            model: self.model.clone(),
            usage,
        }
    }
}

fn call_id(output: &Value) -> Option<String> {
    ["call_id", "id"]
        .iter()
        .filter_map(|k| output.get(*k).and_then(Value::as_str))
        .find(|s| !s.is_empty())
        .map(str::to_string)
}

fn extract_text_from_content(item: &Value) -> Option<String> {
    let non_empty = |key: &str| {
        item.get(key)
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
    };
    let content_type = item.get("type").and_then(Value::as_str).unwrap_or("");
    if matches!(content_type, "output_text" | "input_text" | "text")
        && let Some(text) = non_empty("text")
    {
        return Some(text);
    }
    if content_type == "refusal"
        && let Some(refusal) = non_empty("refusal")
    {
        return Some(refusal);
    }
    non_empty("text").or_else(|| non_empty("content"))
}

fn with_model_hint(message: String) -> String {
    if message.to_lowercase().contains("model") || message.contains("404") {
        format!("{message} Check the configured OpenAI model or run \"hi --config\".")
    } else {
        message
    }
}

fn message_item(role: &str, content: &str) -> Value {
    let part_type = if role == "assistant" {
        "output_text"
    } else {
        "input_text"
    };
    json!({"type": "message", "role": role, "content": [{"type": part_type, "text": content}]})
}

/// Convert chat-style messages (including tool calls/results) into Responses API input items.
pub fn format_messages_for_responses(messages: &[ChatMessage]) -> Vec<Value> {
    let mut items = Vec::new();
    for message in messages {
        let content = message.text();
        let role = match message.role {
            Role::System => "system",
            Role::User => "user",
            Role::Assistant => "assistant",
            Role::Tool => "tool",
        };
        if message.role != Role::Tool && !content.is_empty() {
            items.push(message_item(role, content));
        }
        for tool_call in &message.tool_calls {
            if tool_call.call_id.is_empty() || tool_call.name.is_empty() {
                continue;
            }
            items.push(json!({
                "type": "function_call",
                "call_id": tool_call.call_id,
                "name": tool_call.name,
                "arguments": tool_call.arguments.to_string(),
            }));
        }
        if message.role == Role::Tool {
            match message.tool_call_id.as_deref().filter(|id| !id.is_empty()) {
                Some(call_id) => items.push(json!({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": content,
                })),
                None if !content.is_empty() => items.push(message_item("user", &format!("Tool result: {content}"))),
                None => {}
            }
        }
    }
    items
}

#[async_trait(?Send)]
impl Provider for OpenAiProvider {
    async fn generate_response(
        &self,
        messages: &[ChatMessage],
        tools: Option<&[ToolSpec]>,
        stream_handler: Option<StreamHandler<'_>>,
    ) -> LlmResponse {
        let stream = self.config.streaming && stream_handler.is_some();
        let body = self.build_request(messages, tools, stream);
        let url = format!("{}/responses", self.base_url);
        let result = send_with_retry(
            || self.client.post(&url).bearer_auth(&self.api_key).json(&body),
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
                return LlmResponse::text(format!("OpenAI API error: {message}"), &self.model);
            }
        };
        if !response.status().is_success() {
            return self.error_response(&ApiError::from_response(response).await);
        }

        match stream_handler.filter(|_| stream) {
            Some(handler) => match self.stream_response(response, handler).await {
                Ok((final_response, streamed)) => self.parse_response(&final_response, &streamed),
                Err(e) => LlmResponse::text(format!("OpenAI API error: {e}"), &self.model),
            },
            None => match response.json::<Value>().await {
                Ok(value) => self.parse_response(&value, ""),
                Err(e) => LlmResponse::text(format!("Unexpected error: {e}"), &self.model),
            },
        }
    }

    fn model_name(&self) -> &str {
        &self.model
    }

    fn system_prompt(&self) -> String {
        prompts::openai(&self.config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_tool_cycle_without_invalid_assistant_ids() {
        let messages = vec![
            ChatMessage::user("list files"),
            ChatMessage::assistant_tool_calls(
                None,
                vec![ToolCall::new(
                    "execute_shell_command",
                    json!({"command": "ls"}),
                    Some("call_1".into()),
                )],
            ),
            ChatMessage::tool_result("call_1", "a.txt"),
            ChatMessage::assistant("Here are the files."),
        ];
        let items = format_messages_for_responses(&messages);
        assert_eq!(
            items[0],
            json!({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "list files"}]})
        );
        assert_eq!(
            items[1],
            json!({"type": "function_call", "call_id": "call_1", "name": "execute_shell_command", "arguments": "{\"command\":\"ls\"}"})
        );
        assert_eq!(
            items[2],
            json!({"type": "function_call_output", "call_id": "call_1", "output": "a.txt"})
        );
        assert_eq!(
            items[3],
            json!({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Here are the files."}]})
        );
        assert!(items.iter().all(|i| i.get("id").is_none()));
    }

    #[test]
    fn builds_request_with_instructions_and_tools() {
        let config = HashConfig::default();
        let provider = OpenAiProvider::with_base_url(&config, "http://localhost");
        let tools = crate::llm::available_tools(&config, Some("list files"));
        let request = provider.build_request(
            &[ChatMessage::system("SYS"), ChatMessage::user("hi")],
            Some(&tools),
            false,
        );
        assert_eq!(request["instructions"], "SYS");
        assert_eq!(request["model"], "gpt-5-nano");
        assert_eq!(request["reasoning"], json!({"effort": "low"}));
        assert_eq!(request["text"], json!({"format": {"type": "text"}, "verbosity": "low"}));
        assert_eq!(request["tool_choice"], "auto");
        assert_eq!(request["tools"][0]["strict"], true);
        assert_eq!(request["tools"][0]["name"], "lookup_tldr_command");
        assert_eq!(request["input"].as_array().unwrap().len(), 1);
        assert!(request.get("stream").is_none());
    }

    #[test]
    fn parses_outputs() {
        let provider = OpenAiProvider::with_base_url(&HashConfig::default(), "http://localhost");
        let response = json!({
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": [{"text": "thinking"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "Hello"}, {"type": "refusal", "refusal": " no"}]},
                {"type": "function_call", "call_id": "c1", "name": "execute_shell_command", "arguments": "{\"command\":\"ls\"}"},
                {"type": "custom_tool_call", "id": "c2", "name": "raw", "input": "not json"}
            ],
            "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
        });
        let parsed = provider.parse_response(&response, "");
        assert_eq!(parsed.content, "Hello no");
        assert_eq!(parsed.tool_calls.len(), 2);
        assert_eq!(parsed.tool_calls[0].call_id, "c1");
        assert_eq!(parsed.tool_calls[0].arguments, json!({"command": "ls"}));
        assert_eq!(parsed.tool_calls[1].arguments, json!({"input": "not json"}));
        assert_eq!(parsed.usage.unwrap()["total_tokens"], 7);

        let reasoning_only = json!({"output": [{"type": "reasoning", "summary": [{"text": "a"}, {"text": "b"}]}]});
        assert_eq!(
            provider.parse_response(&reasoning_only, "").content,
            "Reasoning summary (no final text output): a b"
        );
        let empty =
            json!({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []});
        assert!(provider.parse_response(&empty, "").content.starts_with(
            "No response generated by OpenAI. (status: incomplete) (incomplete: {\"reason\":\"max_output_tokens\"})"
        ));
        let error = json!({"error": {"code": "server_error", "message": "boom"}});
        assert_eq!(
            provider.parse_response(&error, "").content,
            "OpenAI response error (server_error): boom"
        );
    }

    #[test]
    fn maps_http_errors() {
        let provider = OpenAiProvider::with_base_url(&HashConfig::default(), "http://localhost");
        let rate = provider.error_response(&ApiError {
            status: 429,
            body: "{}".into(),
        });
        assert!(rate.content.starts_with("OpenAI rate limit exceeded."));
        let auth = provider.error_response(&ApiError {
            status: 401,
            body: "{}".into(),
        });
        assert!(
            auth.content
                .starts_with("Authentication failed. Please check your OpenAI API key.")
        );
        let missing = provider.error_response(&ApiError {
            status: 404,
            body: "{\"error\":{\"message\":\"The model does not exist\"}}".into(),
        });
        assert!(missing.content.starts_with("OpenAI API error: Error code: 404 - "));
        assert!(
            missing
                .content
                .ends_with("Check the configured OpenAI model or run \"hi --config\".")
        );
    }
}
