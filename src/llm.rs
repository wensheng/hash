//! LLM orchestration: conversation context, tool exposure, and the tool-calling loop.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::config::HashConfig;
use crate::history::ConversationHistory;
use crate::providers::{self, Provider, StreamHandler, reborrow};
use crate::tools::{self, ShellTool, Tool};
use crate::ui;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    System,
    User,
    Assistant,
    Tool,
}

impl Role {
    pub fn parse(role: &str) -> Option<Role> {
        match role {
            "system" => Some(Role::System),
            "user" => Some(Role::User),
            "assistant" => Some(Role::Assistant),
            "tool" => Some(Role::Tool),
            _ => None,
        }
    }
}

/// A tool call requested by the model.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolCall {
    pub name: String,
    pub arguments: Value,
    pub call_id: String,
    /// Provider-specific data that must be echoed back (e.g. Gemini thought signatures).
    pub metadata: Map<String, Value>,
}

impl ToolCall {
    pub fn new(name: impl Into<String>, arguments: Value, call_id: Option<String>) -> Self {
        let name = name.into();
        let call_id = call_id
            .filter(|id| !id.is_empty())
            .unwrap_or_else(|| default_call_id(&name, &arguments));
        ToolCall {
            name,
            arguments,
            call_id,
            metadata: Map::new(),
        }
    }
}

pub fn default_call_id(name: &str, arguments: &Value) -> String {
    let mut hasher = DefaultHasher::new();
    arguments.to_string().hash(&mut hasher);
    format!("call_{name}_{}", hasher.finish())
}

/// A conversation message in provider-neutral (OpenAI-like) form.
#[derive(Debug, Clone, PartialEq)]
pub struct ChatMessage {
    pub role: Role,
    pub content: Option<String>,
    pub tool_calls: Vec<ToolCall>,
    pub tool_call_id: Option<String>,
}

impl ChatMessage {
    pub fn new(role: Role, content: impl Into<String>) -> Self {
        ChatMessage {
            role,
            content: Some(content.into()),
            tool_calls: Vec::new(),
            tool_call_id: None,
        }
    }

    pub fn system(content: impl Into<String>) -> Self {
        Self::new(Role::System, content)
    }

    pub fn user(content: impl Into<String>) -> Self {
        Self::new(Role::User, content)
    }

    pub fn assistant(content: impl Into<String>) -> Self {
        Self::new(Role::Assistant, content)
    }

    pub fn assistant_tool_calls(content: Option<String>, tool_calls: Vec<ToolCall>) -> Self {
        ChatMessage {
            role: Role::Assistant,
            content,
            tool_calls,
            tool_call_id: None,
        }
    }

    pub fn tool_result(call_id: impl Into<String>, content: impl Into<String>) -> Self {
        ChatMessage {
            role: Role::Tool,
            content: Some(content.into()),
            tool_calls: Vec::new(),
            tool_call_id: Some(call_id.into()),
        }
    }

    pub fn text(&self) -> &str {
        self.content.as_deref().unwrap_or("")
    }
}

/// Tool definition offered to the model (JSON-schema parameters).
#[derive(Debug, Clone, PartialEq)]
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    pub parameters: Value,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct LlmResponse {
    pub content: String,
    pub tool_calls: Vec<ToolCall>,
    pub model: String,
    pub usage: Option<Value>,
}

impl LlmResponse {
    pub fn text(content: impl Into<String>, model: &str) -> Self {
        LlmResponse {
            content: content.into(),
            model: model.to_string(),
            ..Default::default()
        }
    }

    pub fn has_tool_calls(&self) -> bool {
        !self.tool_calls.is_empty()
    }
}

static EXPLANATORY_QUERY: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?:(?:how to|how do i|how can i|what command|which command)\b|(?:explain|what does|how does)\b)")
        .unwrap()
});

const MAX_TOOL_ROUNDS: usize = 3;
const CONTEXT_MESSAGES: usize = 20;

pub fn tldr_tool_spec() -> ToolSpec {
    ToolSpec {
        name: "lookup_tldr_command".into(),
        description: "Look up concise tldr examples and usage notes for a shell command. \
             Use this when answering command-specific questions and you need grounded syntax or examples."
            .into(),
        parameters: json!({
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to look up, such as tar, find, or xargs."
                },
                "platform": {
                    "type": ["string", "null"],
                    "description": "Optional platform override such as linux, osx, or windows."
                },
                "language": {
                    "type": ["string", "null"],
                    "description": "Optional language override for tldr lookup."
                },
                "search": {
                    "type": "boolean",
                    "description": "Set to true to search commands by keyword instead of looking up one exact command."
                }
            },
            "required": ["command", "platform", "language", "search"],
            "additionalProperties": false
        }),
    }
}

pub fn shell_tool_spec() -> ToolSpec {
    ToolSpec {
        name: "execute_shell_command".into(),
        description: "Execute a shell command and return its output. Use with caution.".into(),
        parameters: json!({
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "description": {"type": "string", "description": "Brief description of what this command does"}
            },
            "required": ["command", "description"],
            "additionalProperties": false
        }),
    }
}

/// Hide direct execution for explanatory command-help queries.
pub fn should_expose_shell_tool(user_message: Option<&str>) -> bool {
    let normalized = user_message.unwrap_or("").trim().to_lowercase();
    normalized.is_empty() || !EXPLANATORY_QUERY.is_match(&normalized)
}

pub fn available_tools(config: &HashConfig, user_message: Option<&str>) -> Vec<ToolSpec> {
    let mut tools = vec![tldr_tool_spec()];
    if config.allow_command_execution && should_expose_shell_tool(user_message) {
        tools.push(shell_tool_spec());
    }
    tools
}

struct ToolResult {
    call_id: String,
    tool_name: String,
    output: String,
}

pub type ToolResolver = fn(&str) -> Option<Box<dyn Tool>>;
pub type ConfirmHook = Box<dyn FnMut(&ToolCall) -> bool>;

pub struct LlmHandler {
    pub config: HashConfig,
    pub provider: Box<dyn Provider>,
    history: Option<ConversationHistory>,
    pub current_session_id: Option<String>,
    pub last_tool_calls_executed: bool,
    tool_resolver: ToolResolver,
    confirm_hook: Option<ConfirmHook>,
}

impl LlmHandler {
    pub fn new(config: &HashConfig, session_id: Option<String>) -> anyhow::Result<Self> {
        Self::with_provider(config, providers::create_provider(config), session_id)
    }

    pub fn with_provider(
        config: &HashConfig,
        provider: Box<dyn Provider>,
        session_id: Option<String>,
    ) -> anyhow::Result<Self> {
        let history = if config.history_enabled {
            Some(ConversationHistory::new(&config.history_dir)?)
        } else {
            None
        };
        let current_session_id = session_id.or_else(|| {
            std::env::var("HASHCLI_SESSION_ID")
                .ok()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
        });
        Ok(LlmHandler {
            config: config.clone(),
            provider,
            history,
            current_session_id,
            last_tool_calls_executed: false,
            tool_resolver: tools::get_tool_executor,
            confirm_hook: None,
        })
    }

    /// Replace tool lookup (used by tests).
    pub fn set_tool_resolver(&mut self, resolver: ToolResolver) {
        self.tool_resolver = resolver;
    }

    /// Replace the interactive tool-call confirmation prompt (used by tests).
    pub fn set_confirm_hook(&mut self, hook: ConfirmHook) {
        self.confirm_hook = Some(hook);
    }

    /// Run one user turn, including any tool calls. Errors are returned as text.
    pub async fn chat(
        &mut self,
        message: &str,
        stream_handler: Option<StreamHandler<'_>>,
        force_tool_confirmation: Option<bool>,
    ) -> String {
        match self.chat_inner(message, stream_handler, force_tool_confirmation).await {
            Ok(content) => content,
            Err(e) if self.config.show_debug => format!("LLM Error: {e:#}\nDebug info: {e:?}"),
            Err(e) => format!("LLM Error: {e:#}"),
        }
    }

    async fn chat_inner(
        &mut self,
        message: &str,
        mut stream_handler: Option<StreamHandler<'_>>,
        force_tool_confirmation: Option<bool>,
    ) -> anyhow::Result<String> {
        self.last_tool_calls_executed = false;
        if !self.config.streaming {
            stream_handler = None;
        }

        if let Some(history) = &self.history {
            let session_id = history.start_session(self.current_session_id.as_deref())?;
            self.current_session_id = Some(session_id.clone());
            history.add_message(&session_id, "user", message)?;
        }

        let context = self.conversation_context(message)?;
        let tools = available_tools(&self.config, Some(message));

        let mut response = self
            .provider
            .generate_response(&context, Some(&tools), reborrow(&mut stream_handler))
            .await;

        if response.has_tool_calls() {
            self.last_tool_calls_executed = true;
            response = self
                .handle_tool_calls(response, context, &tools, stream_handler, force_tool_confirmation)
                .await;
        }

        if let (Some(history), Some(session_id)) = (&self.history, &self.current_session_id) {
            history.add_message(session_id, "assistant", &response.content)?;
        }
        Ok(response.content)
    }

    fn conversation_context(&self, message: &str) -> anyhow::Result<Vec<ChatMessage>> {
        let mut messages = vec![ChatMessage::system(self.provider.system_prompt())];
        match (&self.history, &self.current_session_id) {
            (Some(history), Some(session_id)) => {
                for (role, content) in history.get_recent_messages(session_id, CONTEXT_MESSAGES)? {
                    if let Some(role) = Role::parse(&role) {
                        messages.push(ChatMessage::new(role, content));
                    }
                }
            }
            _ => messages.push(ChatMessage::user(message)),
        }
        Ok(messages)
    }

    async fn handle_tool_calls(
        &mut self,
        response: LlmResponse,
        context: Vec<ChatMessage>,
        tools: &[ToolSpec],
        mut stream_handler: Option<StreamHandler<'_>>,
        force_tool_confirmation: Option<bool>,
    ) -> LlmResponse {
        let mut messages = context;
        let mut current = response;
        let mut last_results: Vec<ToolResult> = Vec::new();

        for _ in 0..MAX_TOOL_ROUNDS {
            if !current.has_tool_calls() {
                return fallback_to_tool_output(current, &last_results, false);
            }

            let mut results = Vec::new();
            for tool_call in &current.tool_calls {
                let output = self.run_tool_call(tool_call, force_tool_confirmation).await;
                results.push(ToolResult {
                    call_id: tool_call.call_id.clone(),
                    tool_name: tool_call.name.clone(),
                    output,
                });
            }

            // Some providers reject empty assistant content alongside tool calls.
            let content = Some(current.content.clone()).filter(|c| !c.is_empty());
            messages.push(ChatMessage::assistant_tool_calls(content, current.tool_calls.clone()));
            for result in &results {
                messages.push(ChatMessage::tool_result(&result.call_id, &result.output));
            }
            last_results = results;

            current = self
                .provider
                .generate_response(&messages, Some(tools), reborrow(&mut stream_handler))
                .await;
        }

        fallback_to_tool_output(current, &last_results, true)
    }

    async fn run_tool_call(&mut self, tool_call: &ToolCall, force_tool_confirmation: Option<bool>) -> String {
        let Some(executor) = (self.tool_resolver)(&tool_call.name) else {
            return format!("Unknown tool: {}", tool_call.name);
        };
        let effective_confirmation = force_tool_confirmation.unwrap_or(self.config.tool_confirmation);
        if should_confirm_tool_call(executor.as_ref(), tool_call, effective_confirmation)
            && !self.confirm_tool_call(tool_call)
        {
            return "User declined to execute this tool call.".to_string();
        }
        match executor.execute(&tool_call.arguments, &self.config).await {
            Ok(output) => output,
            Err(e) => format!("Error executing tool: {e:#}"),
        }
    }

    fn confirm_tool_call(&mut self, tool_call: &ToolCall) -> bool {
        if let Some(hook) = self.confirm_hook.as_mut() {
            return hook(tool_call);
        }
        println!("\n{}", ui::bold_color("Tool Call Request:", console::Color::Yellow));
        println!("Function: {}", ui::cyan(&tool_call.name));
        let args = serde_json::to_string_pretty(&tool_call.arguments).unwrap_or_default();
        println!("Arguments: {}", ui::dim(&args));
        if !ui::is_interactive() {
            println!(
                "{}",
                ui::yellow("Confirmation required; declining in non-interactive mode.")
            );
            return false;
        }
        ui::confirm("Execute this tool call?", false)
    }
}

/// Config-level confirmation plus a hard override for destructive shell commands.
pub fn should_confirm_tool_call(executor: &dyn Tool, tool_call: &ToolCall, effective_confirmation: bool) -> bool {
    if effective_confirmation && executor.requires_confirmation() {
        return true;
    }
    if tool_call.name != "execute_shell_command" {
        return false;
    }
    tool_call
        .arguments
        .get("command")
        .and_then(Value::as_str)
        .is_some_and(ShellTool::is_potentially_destructive_command)
}

fn format_tool_results(results: &[ToolResult]) -> String {
    results
        .iter()
        .map(|r| {
            let name = if r.tool_name.is_empty() { "tool" } else { &r.tool_name };
            let output = if r.output.is_empty() { "No output." } else { &r.output };
            format!("Tool result ({name}):\n{output}")
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

/// Use tool output directly when the model returns no final text.
fn fallback_to_tool_output(
    mut response: LlmResponse,
    results: &[ToolResult],
    reached_round_limit: bool,
) -> LlmResponse {
    if !response.content.trim().is_empty() || results.is_empty() {
        return response;
    }
    let formatted = format_tool_results(results);
    response.content = if reached_round_limit {
        format!(
            "No final response from the model after tool calls. Showing the latest tool output instead.\n\n{formatted}"
        )
    } else {
        formatted
    };
    response
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::tests::EnvGuard;
    use async_trait::async_trait;
    use std::cell::RefCell;
    use std::collections::VecDeque;
    use std::rc::Rc;

    /// (messages sent, tool names offered) for each provider call.
    type RecordedRequests = Rc<RefCell<Vec<(Vec<ChatMessage>, Vec<String>)>>>;

    /// Records requests and replays scripted responses.
    #[derive(Clone, Default)]
    struct MockProvider {
        responses: Rc<RefCell<VecDeque<LlmResponse>>>,
        requests: RecordedRequests,
    }

    impl MockProvider {
        fn new(responses: Vec<LlmResponse>) -> Self {
            MockProvider {
                responses: Rc::new(RefCell::new(responses.into())),
                requests: Rc::default(),
            }
        }
    }

    #[async_trait(?Send)]
    impl Provider for MockProvider {
        async fn generate_response(
            &self,
            messages: &[ChatMessage],
            tools: Option<&[ToolSpec]>,
            stream_handler: Option<StreamHandler<'_>>,
        ) -> LlmResponse {
            let names = tools.unwrap_or(&[]).iter().map(|t| t.name.clone()).collect();
            self.requests.borrow_mut().push((messages.to_vec(), names));
            let response = self
                .responses
                .borrow_mut()
                .pop_front()
                .unwrap_or_else(|| LlmResponse::text("default", "mock"));
            if let Some(handler) = stream_handler {
                handler(&response.content);
            }
            response
        }

        fn model_name(&self) -> &str {
            "mock"
        }

        fn system_prompt(&self) -> String {
            "SYSTEM".into()
        }
    }

    struct EchoTool;

    #[async_trait(?Send)]
    impl Tool for EchoTool {
        fn name(&self) -> &'static str {
            "lookup_tldr_command"
        }
        async fn execute(&self, arguments: &Value, _config: &HashConfig) -> anyhow::Result<String> {
            Ok(format!("tldr page for {}", arguments["command"].as_str().unwrap_or("")))
        }
        fn requires_confirmation(&self) -> bool {
            false
        }
    }

    fn resolver(name: &str) -> Option<Box<dyn Tool>> {
        match name {
            "lookup_tldr_command" => Some(Box::new(EchoTool)),
            "execute_shell_command" => Some(Box::new(ShellTool)),
            _ => None,
        }
    }

    fn config(env: &EnvGuard) -> HashConfig {
        HashConfig {
            history_dir: env.home.path().join("history"),
            ..Default::default()
        }
    }

    fn tool_response(name: &str, args: Value) -> LlmResponse {
        LlmResponse {
            tool_calls: vec![ToolCall::new(name, args, Some(format!("id-{name}")))],
            model: "mock".into(),
            ..Default::default()
        }
    }

    fn handler(cfg: &HashConfig, provider: &MockProvider, session: Option<&str>) -> LlmHandler {
        let mut h = LlmHandler::with_provider(cfg, Box::new(provider.clone()), session.map(str::to_string)).unwrap();
        h.set_tool_resolver(resolver);
        h
    }

    #[tokio::test]
    async fn basic_chat_saves_history() {
        let env = EnvGuard::new();
        let cfg = config(&env);
        let provider = MockProvider::new(vec![LlmResponse::text("Hello!", "mock")]);
        let mut h = handler(&cfg, &provider, None);
        assert_eq!(h.chat("hi there", None, None).await, "Hello!");
        assert!(!h.last_tool_calls_executed);
        let requests = provider.requests.borrow();
        let (messages, tools) = &requests[0];
        assert_eq!(messages[0], ChatMessage::system("SYSTEM"));
        assert_eq!(messages[1], ChatMessage::user("hi there"));
        assert_eq!(
            tools,
            &vec!["lookup_tldr_command".to_string(), "execute_shell_command".to_string()]
        );
        let history = ConversationHistory::new(&cfg.history_dir).unwrap();
        let session = h.current_session_id.clone().unwrap();
        assert_eq!(history.get_session_messages(&session).unwrap().len(), 2);
    }

    #[tokio::test]
    async fn chat_without_history_still_sends_user_message() {
        let env = EnvGuard::new();
        let cfg = HashConfig {
            history_enabled: false,
            ..config(&env)
        };
        let provider = MockProvider::new(vec![LlmResponse::text("ok", "mock")]);
        let mut h = handler(&cfg, &provider, None);
        assert_eq!(h.chat("question", None, None).await, "ok");
        assert_eq!(provider.requests.borrow()[0].0[1], ChatMessage::user("question"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn executes_tool_calls_and_sends_results() {
        let env = EnvGuard::new();
        let cfg = config(&env);
        let provider = MockProvider::new(vec![
            tool_response(
                "execute_shell_command",
                json!({"command": "echo hi", "description": "say hi"}),
            ),
            LlmResponse::text("The command printed hi.", "mock"),
        ]);
        let mut h = handler(&cfg, &provider, None);
        assert_eq!(h.chat("run echo hi", None, None).await, "The command printed hi.");
        assert!(h.last_tool_calls_executed);
        let requests = provider.requests.borrow();
        let follow_up = &requests[1].0;
        let assistant = &follow_up[follow_up.len() - 2];
        assert_eq!(assistant.role, Role::Assistant);
        assert_eq!(assistant.content, None);
        assert_eq!(assistant.tool_calls[0].call_id, "id-execute_shell_command");
        let tool = follow_up.last().unwrap();
        assert_eq!(tool, &ChatMessage::tool_result("id-execute_shell_command", "hi"));
    }

    #[tokio::test]
    async fn tldr_tool_and_unknown_tool() {
        let env = EnvGuard::new();
        let cfg = config(&env);
        let provider = MockProvider::new(vec![
            LlmResponse {
                tool_calls: vec![
                    ToolCall::new("lookup_tldr_command", json!({"command": "tar"}), Some("a".into())),
                    ToolCall::new("mystery", json!({}), Some("b".into())),
                ],
                ..Default::default()
            },
            LlmResponse::text("Use tar -xzf.", "mock"),
        ]);
        let mut h = handler(&cfg, &provider, None);
        assert_eq!(h.chat("explain tar", None, None).await, "Use tar -xzf.");
        let requests = provider.requests.borrow();
        let follow_up = &requests[1].0;
        assert_eq!(
            follow_up[follow_up.len() - 2],
            ChatMessage::tool_result("a", "tldr page for tar")
        );
        assert_eq!(
            follow_up[follow_up.len() - 1],
            ChatMessage::tool_result("b", "Unknown tool: mystery")
        );
    }

    #[tokio::test]
    async fn falls_back_to_tool_output_without_final_text() {
        let env = EnvGuard::new();
        let cfg = config(&env);
        let provider = MockProvider::new(vec![
            tool_response("lookup_tldr_command", json!({"command": "find"})),
            LlmResponse::text("", "mock"),
        ]);
        let mut h = handler(&cfg, &provider, None);
        assert_eq!(
            h.chat("find examples", None, None).await,
            "Tool result (lookup_tldr_command):\ntldr page for find"
        );
    }

    #[tokio::test]
    async fn round_limit_fallback() {
        let env = EnvGuard::new();
        let cfg = config(&env);
        let provider = MockProvider::new(
            (0..4)
                .map(|_| tool_response("lookup_tldr_command", json!({"command": "ls"})))
                .collect(),
        );
        let mut h = handler(&cfg, &provider, None);
        let out = h.chat("loop", None, None).await;
        assert!(
            out.starts_with("No final response from the model after tool calls."),
            "{out}"
        );
        assert_eq!(provider.requests.borrow().len(), 4);
    }

    #[tokio::test]
    async fn shared_session_reuses_history() {
        let env = EnvGuard::new();
        let cfg = config(&env);
        let provider = MockProvider::new(vec![
            LlmResponse::text("first answer", "mock"),
            LlmResponse::text("second", "mock"),
        ]);
        let mut first = handler(&cfg, &provider, Some("shared-1"));
        first.chat("first question", None, None).await;
        let mut second = handler(&cfg, &provider, Some("shared-1"));
        second.chat("second question", None, None).await;
        let requests = provider.requests.borrow();
        let contents: Vec<&str> = requests[1].0.iter().map(ChatMessage::text).collect();
        assert_eq!(
            contents,
            vec!["SYSTEM", "first question", "first answer", "second question"]
        );
    }

    #[tokio::test]
    async fn session_id_from_environment() {
        let env = EnvGuard::new();
        env.set("HASHCLI_SESSION_ID", "env-session");
        let cfg = config(&env);
        let provider = MockProvider::new(vec![]);
        let h = handler(&cfg, &provider, None);
        assert_eq!(h.current_session_id.as_deref(), Some("env-session"));
    }

    async fn run_with_confirmation(tool_confirmation: bool, force: Option<bool>, command: &str) -> (bool, String) {
        let env = EnvGuard::new();
        let cfg = HashConfig {
            tool_confirmation,
            ..config(&env)
        };
        let provider = MockProvider::new(vec![
            tool_response("execute_shell_command", json!({"command": command, "description": "x"})),
            LlmResponse::text("done", "mock"),
        ]);
        let mut h = handler(&cfg, &provider, None);
        let asked = Rc::new(RefCell::new(false));
        let asked_clone = asked.clone();
        h.set_confirm_hook(Box::new(move |_| {
            *asked_clone.borrow_mut() = true;
            false
        }));
        h.chat("do it", None, force).await;
        let requests = provider.requests.borrow();
        let tool_output = requests[1].0.last().unwrap().text().to_string();
        let was_asked = *asked.borrow();
        (was_asked, tool_output)
    }

    #[tokio::test]
    async fn force_confirmation_overrides_config() {
        let (asked, output) = run_with_confirmation(false, Some(true), "echo hi").await;
        assert!(asked);
        assert_eq!(output, "User declined to execute this tool call.");

        let (asked, _) = run_with_confirmation(true, Some(false), "echo hi").await;
        assert!(!asked);

        let (asked, _) = run_with_confirmation(true, None, "echo hi").await;
        assert!(asked);
    }

    #[tokio::test]
    async fn destructive_commands_always_prompt() {
        let (asked, output) = run_with_confirmation(false, Some(false), "kill 12345").await;
        assert!(asked);
        assert_eq!(output, "User declined to execute this tool call.");
    }

    #[tokio::test]
    async fn how_to_queries_only_expose_tldr() {
        let env = EnvGuard::new();
        let cfg = config(&env);
        let provider = MockProvider::new(vec![LlmResponse::text("Use du.", "mock")]);
        let mut h = handler(&cfg, &provider, None);
        h.chat("How do I check disk usage?", None, None).await;
        assert_eq!(provider.requests.borrow()[0].1, vec!["lookup_tldr_command".to_string()]);
        assert!(!should_expose_shell_tool(Some("explain xargs")));
        assert!(should_expose_shell_tool(Some("show disk usage")));
        assert!(should_expose_shell_tool(None));

        let disabled = HashConfig {
            allow_command_execution: false,
            ..cfg
        };
        assert_eq!(available_tools(&disabled, Some("list files")).len(), 1);
    }

    #[tokio::test]
    async fn streaming_only_when_enabled() {
        let env = EnvGuard::new();
        let provider = MockProvider::new(vec![
            LlmResponse::text("streamed", "mock"),
            LlmResponse::text("quiet", "mock"),
        ]);
        let mut chunks = Vec::new();
        let streaming_cfg = HashConfig {
            streaming: true,
            ..config(&env)
        };
        let mut h = handler(&streaming_cfg, &provider, None);
        let mut collect = |c: &str| chunks.push(c.to_string());
        h.chat("hi", Some(&mut collect), None).await;
        let mut h = handler(&config(&env), &provider, None);
        let mut collect2 = |c: &str| chunks.push(c.to_string());
        h.chat("hi", Some(&mut collect2), None).await;
        assert_eq!(chunks, vec!["streamed"]);
    }

    #[test]
    fn default_call_ids_are_stable() {
        let a = ToolCall::new("x", json!({"a": 1}), None);
        let b = ToolCall::new("x", json!({"a": 1}), Some(String::new()));
        assert_eq!(a.call_id, b.call_id);
        assert!(a.call_id.starts_with("call_x_"));
    }
}
