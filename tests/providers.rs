//! HTTP-level provider tests against a local mock server.

use hashcli::config::{HashConfig, LlmProvider};
use hashcli::llm::{ChatMessage, ToolSpec, available_tools};
use hashcli::providers::{AnthropicProvider, GoogleProvider, OpenAiProvider, Provider};
use serde_json::{Value, json};
use wiremock::matchers::{body_partial_json, header, method, path, query_param};
use wiremock::{Mock, MockServer, Request, ResponseTemplate};

fn config(provider: LlmProvider) -> HashConfig {
    HashConfig {
        llm_provider: provider,
        openai_api_key: Some("sk-openai".into()),
        anthropic_api_key: Some("sk-ant".into()),
        google_api_key: Some("g-key".into()),
        google_model: "gemini-test".into(),
        ..Default::default()
    }
}

fn streaming(mut config: HashConfig) -> HashConfig {
    config.streaming = true;
    config
}

fn sse(events: &[Value]) -> ResponseTemplate {
    let body: String = events.iter().map(|e| format!("data: {e}\n\n")).collect();
    ResponseTemplate::new(200)
        .insert_header("content-type", "text/event-stream")
        .set_body_string(body)
}

fn tools(config: &HashConfig) -> Vec<ToolSpec> {
    available_tools(config, Some("list files"))
}

fn conversation() -> Vec<ChatMessage> {
    vec![ChatMessage::system("SYS"), ChatMessage::user("list files")]
}

fn request_json(request: &Request) -> Value {
    serde_json::from_slice(&request.body).unwrap()
}

#[tokio::test]
async fn openai_parses_text_and_function_calls() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/responses"))
        .and(header("authorization", "Bearer sk-openai"))
        .and(body_partial_json(
            json!({"model": "gpt-5-nano", "instructions": "SYS", "tool_choice": "auto"}),
        ))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Listing files."}]},
                {"type": "function_call", "call_id": "call_9", "name": "execute_shell_command",
                 "arguments": "{\"command\":\"ls\",\"description\":\"list\"}"}
            ],
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
        })))
        .expect(1)
        .mount(&server)
        .await;

    let cfg = config(LlmProvider::Openai);
    let provider = OpenAiProvider::with_base_url(&cfg, &format!("{}/v1", server.uri()));
    let response = provider
        .generate_response(&conversation(), Some(&tools(&cfg)), None)
        .await;
    assert_eq!(response.content, "Listing files.");
    assert_eq!(response.tool_calls[0].call_id, "call_9");
    assert_eq!(response.tool_calls[0].arguments["command"], "ls");

    let requests = server.received_requests().await.unwrap();
    let body = request_json(&requests[0]);
    assert_eq!(body["input"][0]["content"][0]["type"], "input_text");
    assert_eq!(body["tools"].as_array().unwrap().len(), 2);
    assert!(body.get("stream").is_none());
}

#[tokio::test]
async fn openai_streams_deltas() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/responses"))
        .and(body_partial_json(json!({"stream": true})))
        .respond_with(sse(&[
            json!({"type": "response.output_text.delta", "delta": "Hel"}),
            json!({"type": "response.output_text.delta", "delta": "lo"}),
            json!({"type": "response.completed", "response": {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]
            }}),
        ]))
        .mount(&server)
        .await;

    let cfg = streaming(config(LlmProvider::Openai));
    let provider = OpenAiProvider::with_base_url(&cfg, &format!("{}/v1", server.uri()));
    let mut chunks = Vec::new();
    let mut handler = |c: &str| chunks.push(c.to_string());
    let response = provider
        .generate_response(&conversation(), None, Some(&mut handler))
        .await;
    assert_eq!(response.content, "Hello");
    assert_eq!(chunks, vec!["Hel", "lo"]);
}

#[tokio::test]
async fn openai_maps_errors_and_retries() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(401).set_body_json(json!({"error": {"message": "bad key"}})))
        .mount(&server)
        .await;
    let cfg = config(LlmProvider::Openai);
    let mut provider = OpenAiProvider::with_base_url(&cfg, &server.uri());
    provider.max_retries = 0;
    let response = provider.generate_response(&conversation(), None, None).await;
    assert!(
        response
            .content
            .starts_with("Authentication failed. Please check your OpenAI API key.")
    );

    let flaky = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(500))
        .up_to_n_times(1)
        .with_priority(1)
        .mount(&flaky)
        .await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "recovered"}]}]
        })))
        .with_priority(2)
        .mount(&flaky)
        .await;
    let provider = OpenAiProvider::with_base_url(&cfg, &flaky.uri());
    let response = provider.generate_response(&conversation(), None, None).await;
    assert_eq!(response.content, "recovered");
    assert_eq!(flaky.received_requests().await.unwrap().len(), 2);

    let limited = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(429).set_body_json(json!({"error": {}})))
        .mount(&limited)
        .await;
    let mut provider = OpenAiProvider::with_base_url(&cfg, &limited.uri());
    provider.max_retries = 0;
    let response = provider.generate_response(&conversation(), None, None).await;
    assert!(response.content.starts_with("OpenAI rate limit exceeded."));
}

#[tokio::test]
async fn anthropic_parses_tool_use_with_headers() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/messages"))
        .and(header("x-api-key", "sk-ant"))
        .and(header("anthropic-version", "2023-06-01"))
        .and(body_partial_json(json!({"system": "SYS", "max_tokens": 4096})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "content": [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "toolu_1", "name": "execute_shell_command", "input": {"command": "ls"}}
            ],
            "usage": {"input_tokens": 3, "output_tokens": 4}
        })))
        .expect(1)
        .mount(&server)
        .await;

    let cfg = config(LlmProvider::Anthropic);
    let provider = AnthropicProvider::with_base_url(&cfg, &server.uri());
    let response = provider
        .generate_response(&conversation(), Some(&tools(&cfg)), None)
        .await;
    assert_eq!(response.content, "Checking.");
    assert_eq!(response.tool_calls[0].call_id, "toolu_1");
    let body = request_json(&server.received_requests().await.unwrap()[0]);
    assert_eq!(body["messages"], json!([{"role": "user", "content": "list files"}]));
    assert_eq!(body["tools"][0]["input_schema"]["type"], "object");
}

#[tokio::test]
async fn anthropic_streams_text_and_tool_input() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/messages"))
        .and(body_partial_json(json!({"stream": true})))
        .respond_with(sse(&[
            json!({"type": "message_start", "message": {"usage": {"input_tokens": 5, "output_tokens": 0}}}),
            json!({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            json!({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Run "}}),
            json!({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "this."}}),
            json!({"type": "content_block_stop", "index": 0}),
            json!({"type": "content_block_start", "index": 1,
                   "content_block": {"type": "tool_use", "id": "toolu_2", "name": "execute_shell_command", "input": {}}}),
            json!({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"comm"}}),
            json!({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "and\": \"pwd\"}"}}),
            json!({"type": "content_block_stop", "index": 1}),
            json!({"type": "message_delta", "usage": {"output_tokens": 9}}),
            json!({"type": "message_stop"}),
        ]))
        .mount(&server)
        .await;

    let cfg = streaming(config(LlmProvider::Anthropic));
    let provider = AnthropicProvider::with_base_url(&cfg, &server.uri());
    let mut chunks = Vec::new();
    let mut handler = |c: &str| chunks.push(c.to_string());
    let response = provider
        .generate_response(&conversation(), None, Some(&mut handler))
        .await;
    assert_eq!(response.content, "Run this.");
    assert_eq!(chunks, vec!["Run ", "this."]);
    assert_eq!(response.tool_calls[0].arguments, json!({"command": "pwd"}));
    assert_eq!(response.usage.unwrap()["total_tokens"], 14);
}

#[tokio::test]
async fn google_round_trips_thought_signature_and_disables_tools_for_how_to() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1beta/models/gemini-test:generateContent"))
        .and(header("x-goog-api-key", "g-key"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "execute_shell_command", "args": {"command": "date"}}, "thoughtSignature": "c2ln"}
            ]}}]
        })))
        .mount(&server)
        .await;

    let cfg = config(LlmProvider::Google);
    let provider = GoogleProvider::with_base_url(&cfg, &server.uri());
    let all_tools = available_tools(&cfg, None);
    let response = provider
        .generate_response(&conversation(), Some(&all_tools), None)
        .await;
    let call = response.tool_calls[0].clone();
    assert_eq!(call.metadata.get("thought_signature"), Some(&json!("c2ln")));

    let follow_up = vec![
        ChatMessage::user("what day is it"),
        ChatMessage::assistant_tool_calls(None, vec![call.clone()]),
        ChatMessage::tool_result(&call.call_id, "Mon"),
    ];
    provider.generate_response(&follow_up, Some(&all_tools), None).await;
    let how_to = vec![ChatMessage::user("how do I check the date")];
    provider.generate_response(&how_to, Some(&all_tools), None).await;

    let requests = server.received_requests().await.unwrap();
    let first = request_json(&requests[0]);
    assert!(first.get("toolConfig").is_none());
    assert_eq!(
        first["tools"][0]["functionDeclarations"][0]["parameters"]["type"],
        "OBJECT"
    );
    let second = request_json(&requests[1]);
    assert_eq!(second["contents"][1]["parts"][0]["thoughtSignature"], "c2ln");
    assert_eq!(
        second["contents"][2]["parts"][0]["functionResponse"],
        json!({"name": "execute_shell_command", "response": {"content": "Mon"}})
    );
    let third = request_json(&requests[2]);
    assert_eq!(third["toolConfig"], json!({"functionCallingConfig": {"mode": "NONE"}}));
}

#[tokio::test]
async fn google_streams_and_reports_invalid_key() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1beta/models/gemini-test:streamGenerateContent"))
        .and(query_param("alt", "sse"))
        .respond_with(sse(&[
            json!({"candidates": [{"content": {"parts": [{"text": "Hi "}]}}]}),
            json!({"candidates": [{"content": {"parts": [{"text": "there"}]}}],
                   "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2, "totalTokenCount": 3}}),
        ]))
        .mount(&server)
        .await;
    let cfg = streaming(config(LlmProvider::Google));
    let provider = GoogleProvider::with_base_url(&cfg, &server.uri());
    let mut chunks = Vec::new();
    let mut handler = |c: &str| chunks.push(c.to_string());
    let response = provider
        .generate_response(&conversation(), None, Some(&mut handler))
        .await;
    assert_eq!(response.content, "Hi there");
    assert_eq!(chunks, vec!["Hi ", "there"]);

    let bad = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(400).set_body_json(json!({
            "error": {"code": 400, "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}
        })))
        .mount(&bad)
        .await;
    let mut provider = GoogleProvider::with_base_url(&config(LlmProvider::Google), &bad.uri());
    provider.max_retries = 0;
    let response = provider.generate_response(&conversation(), None, None).await;
    assert!(
        response
            .content
            .starts_with("Google AI error: Invalid Google AI API key."),
        "{}",
        response.content
    );
}
