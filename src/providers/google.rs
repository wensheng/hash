//! Google Gemini provider (Generative Language API).

use std::collections::{BTreeSet, HashMap};
use std::sync::LazyLock;

use async_trait::async_trait;
use regex::Regex;
use serde_json::{Map, Value, json};

use super::sse::SseStream;
use super::{
    ApiError, DEFAULT_MAX_RETRIES, Provider, StreamHandler, env_base_url, http_client, prompts, send_with_retry,
};
use crate::config::HashConfig;
use crate::llm::{ChatMessage, LlmResponse, Role, ToolCall, ToolSpec, default_call_id};

const DEFAULT_BASE_URL: &str = "https://generativelanguage.googleapis.com";

static HOW_TO_QUERY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(?:how to|how do i|how can i|what command|which command)\b").unwrap());
static JSON_BLOCK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)```json\s*(\{.*?\})\s*```").unwrap());

const SAFETY_CATEGORIES: &[&str] = &[
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
];

pub struct GoogleProvider {
    config: HashConfig,
    model: String,
    api_key: String,
    base_url: String,
    client: reqwest::Client,
    pub max_retries: u32,
}

impl GoogleProvider {
    pub fn new(config: &HashConfig) -> Self {
        let base_url = env_base_url("GOOGLE_GEMINI_BASE_URL").unwrap_or_else(|| DEFAULT_BASE_URL.to_string());
        Self::with_base_url(config, &base_url)
    }

    pub fn with_base_url(config: &HashConfig, base_url: &str) -> Self {
        GoogleProvider {
            config: config.clone(),
            model: config.google_model.clone(),
            api_key: config.google_api_key.clone().unwrap_or_default(),
            base_url: base_url.trim_end_matches('/').to_string(),
            client: http_client(),
            max_retries: DEFAULT_MAX_RETRIES,
        }
    }

    fn is_gemma_model(&self) -> bool {
        self.model.to_lowercase().contains("gemma")
    }

    fn build_request(&self, messages: &[ChatMessage], tools: Option<&[ToolSpec]>) -> Value {
        let disable_tool_calls = should_disable_tool_calls(messages, tools);
        let tools = tools.filter(|t| !t.is_empty());

        let mut google_tools = None;
        let contents = match tools {
            Some(tools) if self.is_gemma_model() => {
                if disable_tool_calls {
                    self.format_messages(messages)
                } else {
                    let tool_prompt = format_tools_for_system_prompt(tools);
                    let mut copy = messages.to_vec();
                    match copy.iter_mut().find(|m| m.role == Role::System) {
                        Some(system) => system.content = Some(format!("{}{tool_prompt}", system.text())),
                        None => copy.insert(0, ChatMessage::system(tool_prompt)),
                    }
                    self.format_messages(&copy)
                }
            }
            Some(tools) => {
                google_tools = Some(convert_tools(tools));
                self.format_messages(messages)
            }
            None => self.format_messages(messages),
        };

        let mut request = json!({
            "contents": contents,
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": self.config.max_response_tokens,
            },
            "safetySettings": SAFETY_CATEGORIES
                .iter()
                .map(|c| json!({"category": c, "threshold": "BLOCK_ONLY_HIGH"}))
                .collect::<Vec<_>>(),
        });
        if let Some(google_tools) = google_tools.filter(|t| !t.is_empty()) {
            request["tools"] = Value::Array(google_tools);
            if disable_tool_calls {
                request["toolConfig"] = json!({"functionCallingConfig": {"mode": "NONE"}});
            }
        }
        request
    }

    /// Convert neutral messages to Gemini `contents`, merging adjacent same-role turns.
    pub fn format_messages(&self, messages: &[ChatMessage]) -> Vec<Value> {
        let gemma = self.is_gemma_model();
        let mut contents: Vec<(String, Vec<Value>)> = Vec::new();
        let mut tool_names: HashMap<String, String> = HashMap::new();

        for message in messages {
            let content = message.text();
            match message.role {
                Role::System => contents.push(("user".into(), vec![json!({"text": format!("System: {content}")})])),
                Role::User => contents.push(("user".into(), vec![json!({"text": content})])),
                Role::Assistant => {
                    let mut parts = Vec::new();
                    if !content.is_empty() {
                        parts.push(json!({"text": content}));
                    }
                    if !message.tool_calls.is_empty() {
                        for tc in &message.tool_calls {
                            tool_names.insert(tc.call_id.clone(), tc.name.clone());
                        }
                        if gemma {
                            let uses: Vec<Value> = message
                                .tool_calls
                                .iter()
                                .map(|tc| json!({"tool_name": tc.name, "arguments": tc.arguments}))
                                .collect();
                            let block = serde_json::to_string_pretty(&json!({"tool_uses": uses})).unwrap_or_default();
                            parts.push(json!({"text": format!("```json\n{block}\n```")}));
                        } else {
                            for tc in &message.tool_calls {
                                let args = if tc.arguments.is_object() {
                                    tc.arguments.clone()
                                } else {
                                    json!({})
                                };
                                let mut part = json!({
                                    "functionCall": {"id": tc.call_id, "name": tc.name, "args": args}
                                });
                                if let Some(thought) = tc.metadata.get("thought").filter(|v| !v.is_null()) {
                                    part["thought"] = thought.clone();
                                }
                                if let Some(signature) = tc.metadata.get("thought_signature").filter(|v| !v.is_null()) {
                                    part["thoughtSignature"] = signature.clone();
                                }
                                parts.push(part);
                            }
                        }
                    }
                    if !parts.is_empty() {
                        contents.push(("model".into(), parts));
                    }
                }
                Role::Tool => {
                    let name = message.tool_call_id.as_ref().and_then(|id| tool_names.get(id));
                    let part = if gemma {
                        json!({"text": format!("Tool Result ({}):\n{content}", name.map_or("unknown", String::as_str))})
                    } else if let Some(name) = name {
                        json!({"functionResponse": {"name": name, "response": {"content": content}}})
                    } else {
                        json!({"text": format!("Tool Result (unknown function): {content}")})
                    };
                    contents.push(("user".into(), vec![part]));
                }
            }
        }

        let mut merged: Vec<(String, Vec<Value>)> = Vec::new();
        for (role, parts) in contents {
            match merged.last_mut() {
                Some((last_role, last_parts)) if *last_role == role => last_parts.extend(parts),
                _ => merged.push((role, parts)),
            }
        }
        merged
            .into_iter()
            .map(|(role, parts)| json!({"role": role, "parts": parts}))
            .collect()
    }

    fn error_response(&self, error_message: String) -> LlmResponse {
        let lowered = error_message.to_lowercase();
        let message = if error_message.contains("API_KEY_INVALID") {
            "Invalid Google AI API key. Please check your configuration. Run \"hi --config\" to set this up interactively."
                .to_string()
        } else if error_message.contains("429") || lowered.contains("quota") {
            "Google API quota or rate limit exceeded. Please try again later or choose a different model.".to_string()
        } else if lowered.contains("model") || error_message.contains("404") {
            format!("{error_message} Check the configured Google model or run \"hi --config\".")
        } else if lowered.contains("blocked") {
            "Content was blocked by Google's safety filters.".to_string()
        } else {
            error_message
        };
        LlmResponse::text(format!("Google AI error: {message}"), &self.model)
    }

    /// Collect text, tool calls, and usage from response chunks (one chunk when not streaming).
    fn parse_chunks(&self, chunks: &[Value], streamed: &str) -> LlmResponse {
        let gemma = self.is_gemma_model();
        let mut content = streamed.to_string();
        let mut text_from_parts = String::new();
        let mut tool_calls = Vec::new();
        let mut saw_candidate = false;
        let mut usage = None;

        for chunk in chunks {
            if let Some(meta) = chunk.get("usageMetadata").filter(|m| !m.is_null()) {
                let count = |key: &str| meta.get(key).and_then(Value::as_i64).unwrap_or(0);
                usage = Some(json!({
                    "prompt_tokens": count("promptTokenCount"),
                    "completion_tokens": count("candidatesTokenCount"),
                    "total_tokens": count("totalTokenCount"),
                }));
            }
            let Some(candidate) = chunk.pointer("/candidates/0") else {
                continue;
            };
            saw_candidate = true;
            for part in candidate
                .pointer("/content/parts")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let is_thought = part.get("thought").and_then(Value::as_bool).unwrap_or(false);
                if let Some(text) = part.get("text").and_then(Value::as_str).filter(|t| !t.is_empty()) {
                    if !is_thought {
                        text_from_parts.push_str(text);
                    }
                    if gemma {
                        tool_calls.extend(parse_tool_calls_from_text(text));
                    }
                }
                if let Some(call) = part.get("functionCall") {
                    let name = call.get("name").and_then(Value::as_str).unwrap_or("").to_string();
                    let args = call.get("args").cloned().unwrap_or_else(|| json!({}));
                    let id = call
                        .get("id")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                        .unwrap_or_else(|| default_call_id(&name, &args));
                    let mut tool_call = ToolCall::new(name, args, Some(id));
                    let mut metadata = Map::new();
                    if let Some(thought) = part.get("thought").filter(|v| !v.is_null()) {
                        metadata.insert("thought".into(), thought.clone());
                    }
                    if let Some(signature) = part.get("thoughtSignature").filter(|v| !v.is_null()) {
                        metadata.insert("thought_signature".into(), signature.clone());
                    }
                    tool_call.metadata = metadata;
                    tool_calls.push(tool_call);
                }
            }
        }

        if !saw_candidate {
            return LlmResponse::text(
                "No response generated. Content may have been blocked by safety filters.",
                &self.model,
            );
        }
        if content.is_empty() {
            content = text_from_parts;
        }
        LlmResponse {
            content,
            tool_calls,
            model: self.model.clone(),
            usage,
        }
    }
}

fn latest_user_message(messages: &[ChatMessage]) -> &str {
    messages
        .iter()
        .rev()
        .filter(|m| m.role == Role::User)
        .map(ChatMessage::text)
        .find(|t| !t.trim().is_empty())
        .unwrap_or("")
}

/// Disable tool calling for how-to prompts unless tldr lookup is the only tool.
pub fn should_disable_tool_calls(messages: &[ChatMessage], tools: Option<&[ToolSpec]>) -> bool {
    let latest = latest_user_message(messages);
    if latest.is_empty() || !HOW_TO_QUERY.is_match(&latest.trim().to_lowercase()) {
        return false;
    }
    let Some(tools) = tools.filter(|t| !t.is_empty()) else {
        return true;
    };
    let names: BTreeSet<&str> = tools.iter().map(|t| t.name.as_str()).collect();
    !names.is_empty() && names != BTreeSet::from(["lookup_tldr_command"])
}

fn convert_tools(tools: &[ToolSpec]) -> Vec<Value> {
    let declarations: Vec<Value> = tools
        .iter()
        .map(|t| json!({"name": t.name, "description": t.description, "parameters": clean_parameter_schema(&t.parameters)}))
        .collect();
    if declarations.is_empty() {
        Vec::new()
    } else {
        vec![json!({"functionDeclarations": declarations})]
    }
}

/// Remove unsupported schema fields and normalize types for Gemini.
pub fn clean_parameter_schema(schema: &Value) -> Value {
    let Some(map) = schema.as_object() else {
        return schema.clone();
    };
    let mut cleaned = Map::new();
    for (key, value) in map {
        if key == "type" {
            let normalized = match value {
                Value::Array(types) => types
                    .iter()
                    .filter_map(Value::as_str)
                    .find(|t| *t != "null")
                    .map(|t| json!(t.to_uppercase()))
                    .unwrap_or_else(|| json!("NULL")),
                Value::String(t) => json!(t.to_uppercase()),
                other => other.clone(),
            };
            cleaned.insert(key.clone(), normalized);
            continue;
        }
        if key == "additionalProperties" || key == "additional_properties" {
            continue;
        }
        let value = match value {
            Value::Object(_) => clean_parameter_schema(value),
            Value::Array(items) => Value::Array(
                items
                    .iter()
                    .map(|item| {
                        if item.is_object() {
                            clean_parameter_schema(item)
                        } else {
                            item.clone()
                        }
                    })
                    .collect(),
            ),
            other => other.clone(),
        };
        cleaned.insert(key.clone(), value);
    }
    Value::Object(cleaned)
}

fn format_tools_for_system_prompt(tools: &[ToolSpec]) -> String {
    let mut prompt = String::from("\n\nAVAILABLE TOOLS:\n");
    for tool in tools {
        let params = serde_json::to_string_pretty(&tool.parameters).unwrap_or_default();
        prompt.push_str(&format!(
            "- Name: {}\n  Description: {}\n  Parameters: {params}\n\n",
            tool.name, tool.description
        ));
    }
    prompt.push_str("TOOL USAGE INSTRUCTIONS:\n");
    prompt.push_str("To use a tool, you MUST respond with a JSON object wrapped in markdown code blocks like this:\n");
    prompt.push_str("```json\n");
    prompt.push_str(
        "{\n  \"tool_uses\": [\n    {\n      \"tool_name\": \"example_tool\",\n      \"arguments\": {\n        \"param1\": \"value1\"\n      }\n    }\n  ]\n}\n",
    );
    prompt.push_str("```\n");
    prompt.push_str("If you are not using a tool, do not output this JSON format.");
    prompt
}

fn parse_tool_calls_from_text(text: &str) -> Vec<ToolCall> {
    let mut calls = Vec::new();
    for caps in JSON_BLOCK.captures_iter(text) {
        let Ok(data) = serde_json::from_str::<Value>(&caps[1]) else {
            continue;
        };
        for tool_use in data.get("tool_uses").and_then(Value::as_array).into_iter().flatten() {
            let Some(name) = tool_use
                .get("tool_name")
                .and_then(Value::as_str)
                .filter(|n| !n.is_empty())
            else {
                continue;
            };
            let args = tool_use.get("arguments").cloned().unwrap_or_else(|| json!({}));
            calls.push(ToolCall::new(name, args, None));
        }
    }
    calls
}

#[async_trait(?Send)]
impl Provider for GoogleProvider {
    async fn generate_response(
        &self,
        messages: &[ChatMessage],
        tools: Option<&[ToolSpec]>,
        stream_handler: Option<StreamHandler<'_>>,
    ) -> LlmResponse {
        let stream = self.config.streaming && stream_handler.is_some();
        let body = self.build_request(messages, tools);
        let url = if stream {
            format!(
                "{}/v1beta/models/{}:streamGenerateContent?alt=sse",
                self.base_url, self.model
            )
        } else {
            format!("{}/v1beta/models/{}:generateContent", self.base_url, self.model)
        };
        let result = send_with_retry(
            || {
                self.client
                    .post(&url)
                    .header("x-goog-api-key", &self.api_key)
                    .json(&body)
            },
            self.max_retries,
        )
        .await;
        let response = match result {
            Ok(r) => r,
            Err(e) => return self.error_response(e.to_string()),
        };
        if !response.status().is_success() {
            let status = response.status();
            let error = ApiError::from_response(response).await;
            let reason = status.canonical_reason().unwrap_or("");
            return self.error_response(format!("{} {reason}. {}", error.status, error.compact_body()));
        }

        match stream_handler.filter(|_| stream) {
            Some(handler) => {
                let mut events = SseStream::new(response);
                let mut chunks = Vec::new();
                let mut streamed = String::new();
                while let Some(event) = events.next_event().await {
                    let event = match event {
                        Ok(event) => event,
                        Err(e) => return self.error_response(e),
                    };
                    let Ok(chunk) = serde_json::from_str::<Value>(&event.data) else {
                        continue;
                    };
                    for part in chunk
                        .pointer("/candidates/0/content/parts")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                    {
                        let is_thought = part.get("thought").and_then(Value::as_bool).unwrap_or(false);
                        if let Some(text) = part
                            .get("text")
                            .and_then(Value::as_str)
                            .filter(|t| !t.is_empty() && !is_thought)
                        {
                            streamed.push_str(text);
                            handler(text);
                        }
                    }
                    chunks.push(chunk);
                }
                self.parse_chunks(&chunks, &streamed)
            }
            None => match response.json::<Value>().await {
                Ok(value) => self.parse_chunks(&[value], ""),
                Err(e) => self.error_response(e.to_string()),
            },
        }
    }

    fn model_name(&self) -> &str {
        &self.model
    }

    fn system_prompt(&self) -> String {
        prompts::google(&self.config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn provider(model: &str) -> GoogleProvider {
        let config = HashConfig {
            google_model: model.into(),
            google_api_key: Some("k".into()),
            ..Default::default()
        };
        GoogleProvider::with_base_url(&config, "http://localhost")
    }

    #[test]
    fn formats_tool_cycle() {
        let mut call = ToolCall::new("execute_shell_command", json!({"command": "ls"}), Some("call_1".into()));
        call.metadata.insert("thought_signature".into(), json!("c2lnbmF0dXJl"));
        let messages = vec![
            ChatMessage::system("SYS"),
            ChatMessage::user("list files"),
            ChatMessage::assistant_tool_calls(None, vec![call]),
            ChatMessage::tool_result("call_1", "a.txt"),
            ChatMessage::assistant("Done"),
        ];
        let contents = provider("gemini-2.5-flash").format_messages(&messages);
        assert_eq!(contents.len(), 4);
        assert_eq!(contents[0]["role"], "user");
        assert_eq!(contents[0]["parts"][0]["text"], "System: SYS");
        assert_eq!(contents[0]["parts"][1]["text"], "list files");
        assert_eq!(contents[1]["role"], "model");
        assert_eq!(
            contents[1]["parts"][0],
            json!({"functionCall": {"id": "call_1", "name": "execute_shell_command", "args": {"command": "ls"}}, "thoughtSignature": "c2lnbmF0dXJl"})
        );
        assert_eq!(
            contents[2]["parts"][0],
            json!({"functionResponse": {"name": "execute_shell_command", "response": {"content": "a.txt"}}})
        );
        assert_eq!(contents[3], json!({"role": "model", "parts": [{"text": "Done"}]}));
    }

    #[test]
    fn unknown_tool_result_and_gemma_format() {
        let messages = vec![ChatMessage::tool_result("missing", "out")];
        let contents = provider("gemini-2.5-flash").format_messages(&messages);
        assert_eq!(contents[0]["parts"][0]["text"], "Tool Result (unknown function): out");

        let gemma = provider("gemma-3-27b-it");
        let messages = vec![
            ChatMessage::assistant_tool_calls(None, vec![ToolCall::new("x", json!({"a": 1}), Some("id1".into()))]),
            ChatMessage::tool_result("id1", "result"),
        ];
        let contents = gemma.format_messages(&messages);
        assert!(
            contents[0]["parts"][0]["text"]
                .as_str()
                .unwrap()
                .starts_with("```json\n{\n  \"tool_uses\"")
        );
        assert_eq!(contents[1]["parts"][0]["text"], "Tool Result (x):\nresult");
    }

    #[test]
    fn disables_function_calling_for_how_to() {
        let config = HashConfig::default();
        let how_to = vec![ChatMessage::user("How do I find large files?")];
        let action = vec![ChatMessage::user("find large files")];
        let all_tools = crate::llm::available_tools(&config, None);
        let tldr_only = vec![crate::llm::tldr_tool_spec()];

        let p = provider("gemini-2.5-flash");
        let request = p.build_request(&how_to, Some(&all_tools));
        assert_eq!(
            request["toolConfig"],
            json!({"functionCallingConfig": {"mode": "NONE"}})
        );
        let request = p.build_request(&action, Some(&all_tools));
        assert!(request.get("toolConfig").is_none());
        let request = p.build_request(&how_to, Some(&tldr_only));
        assert!(request.get("toolConfig").is_none());
        assert!(should_disable_tool_calls(&how_to, None));
        assert_eq!(request["generationConfig"]["maxOutputTokens"], 4096);
        assert_eq!(request["safetySettings"].as_array().unwrap().len(), 4);
    }

    #[test]
    fn gemma_injects_tool_prompt() {
        let config = HashConfig::default();
        let tools = crate::llm::available_tools(&config, None);
        let request =
            provider("gemma-3").build_request(&[ChatMessage::system("SYS"), ChatMessage::user("ls")], Some(&tools));
        assert!(request.get("tools").is_none());
        let system = request["contents"][0]["parts"][0]["text"].as_str().unwrap();
        assert!(system.starts_with("System: SYS\n\nAVAILABLE TOOLS:\n- Name: lookup_tldr_command"));
    }

    #[test]
    fn cleans_parameter_schema() {
        let cleaned = clean_parameter_schema(&crate::llm::tldr_tool_spec().parameters);
        assert_eq!(cleaned["type"], "OBJECT");
        assert!(cleaned.get("additionalProperties").is_none());
        assert_eq!(cleaned["properties"]["platform"]["type"], "STRING");
        assert_eq!(cleaned["properties"]["search"]["type"], "BOOLEAN");
        assert_eq!(clean_parameter_schema(&json!({"type": ["null"]}))["type"], "NULL");
    }

    #[test]
    fn parses_function_calls_with_thought_signature() {
        let p = provider("gemini-3-flash-preview");
        let response = json!({
            "candidates": [{"content": {"role": "model", "parts": [
                {"text": "Checking."},
                {"functionCall": {"name": "execute_shell_command", "args": {"command": "date"}}, "thoughtSignature": "abc123"}
            ]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2, "totalTokenCount": 7}
        });
        let parsed = p.parse_chunks(&[response], "");
        assert_eq!(parsed.content, "Checking.");
        assert_eq!(
            parsed.tool_calls[0].metadata.get("thought_signature"),
            Some(&json!("abc123"))
        );
        assert!(parsed.tool_calls[0].call_id.starts_with("call_execute_shell_command_"));
        assert_eq!(parsed.usage.unwrap()["total_tokens"], 7);

        let blocked = p.parse_chunks(&[json!({"promptFeedback": {"blockReason": "SAFETY"}})], "");
        assert!(blocked.content.starts_with("No response generated."));
    }

    #[test]
    fn parses_gemma_text_tool_calls() {
        let text = "```json\n{\"tool_uses\": [{\"tool_name\": \"lookup_tldr_command\", \"arguments\": {\"command\": \"tar\"}}]}\n```";
        let calls = parse_tool_calls_from_text(text);
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].arguments, json!({"command": "tar"}));
    }

    #[test]
    fn maps_errors() {
        let p = provider("gemini-2.5-flash");
        assert!(
            p.error_response("400 Bad Request. API_KEY_INVALID".into())
                .content
                .contains("Invalid Google AI API key")
        );
        assert!(
            p.error_response("429 Too Many Requests".into())
                .content
                .contains("quota or rate limit")
        );
        assert!(
            p.error_response("404 Not Found. models/x".into())
                .content
                .ends_with("Check the configured Google model or run \"hi --config\".")
        );
    }
}
