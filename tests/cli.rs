//! End-to-end tests that run the `hashcli` binary with an isolated HOME.

use std::collections::VecDeque;
use std::path::Path;
use std::sync::Mutex;

use predicates::prelude::*;
use serde_json::{Value, json};
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, Request, Respond, ResponseTemplate};

fn hashcli(home: &Path) -> assert_cmd::Command {
    let mut cmd = assert_cmd::cargo::cargo_bin_cmd!("hashcli");
    cmd.env_clear()
        .env("PATH", std::env::var("PATH").unwrap_or_default())
        .env("HOME", home)
        .env("USERPROFILE", home)
        .env("SHELL", "/bin/tcsh")
        .env("NO_COLOR", "1");
    cmd
}

fn home() -> tempfile::TempDir {
    tempfile::tempdir().unwrap()
}

#[test]
fn version_help_and_welcome() {
    let home = home();
    hashcli(home.path())
        .arg("--version")
        .assert()
        .success()
        .stdout(format!("Hash CLI version {}\n", env!("CARGO_PKG_VERSION")));
    hashcli(home.path())
        .arg("-h")
        .assert()
        .success()
        .stdout(predicate::str::contains("--add-cmd").and(predicate::str::contains("--new-session")));
    hashcli(home.path())
        .assert()
        .success()
        .stdout(predicate::str::contains("Hash CLI - Command Assistant"));
    hashcli(home.path()).arg("--bogus").assert().code(2);
}

#[test]
fn hi_alias_runs_hashcli() {
    let home = home();
    let mut hi = assert_cmd::cargo::cargo_bin_cmd!("hi");
    hi.env_clear()
        .env("PATH", std::env::var("PATH").unwrap_or_default())
        .env("HOME", home.path())
        .args(["-q", "config", "get", "streaming"])
        .assert()
        .success()
        .stdout("streaming = false\n");
}

#[test]
fn show_config_and_command_mode() {
    let home = home();
    hashcli(home.path())
        .arg("-s")
        .assert()
        .success()
        .stdout(predicate::str::contains("Provider: google").and(predicate::str::contains("API Key: ✗ Not set")));
    hashcli(home.path())
        .arg("/help")
        .assert()
        .success()
        .stdout(predicate::str::contains("CORE COMMANDS:").and(predicate::str::contains(" Result ")));
    hashcli(home.path())
        .args(["-q", "config", "get", "streaming"])
        .assert()
        .success()
        .stdout("streaming = false\n");
    hashcli(home.path())
        .args(["-q", "/nope"])
        .assert()
        .success()
        .stdout("Unknown command: /nope\nUse /help for available commands.\n");
    hashcli(home.path())
        .args(["-q", "# /history list"])
        .assert()
        .success()
        .stdout("No conversation history found.\n");
}

#[test]
fn config_set_preserves_comments() {
    let home = home();
    let config_path = home.path().join(".hashcli/config.toml");
    std::fs::create_dir_all(config_path.parent().unwrap()).unwrap();
    std::fs::write(&config_path, "# my settings\nllm_provider = \"openai\" # main\n").unwrap();
    hashcli(home.path())
        .args(["-q", "/config", "set", "streaming", "true"])
        .assert()
        .success()
        .stdout("Set streaming.\n");
    let text = std::fs::read_to_string(&config_path).unwrap();
    assert!(text.contains("# my settings"));
    assert!(text.contains("llm_provider = \"openai\" # main"));
    assert!(text.contains("streaming = true"));
    hashcli(home.path())
        .args(["-q", "config", "get", "streaming"])
        .assert()
        .stdout("streaming = true\n");
}

#[test]
fn missing_api_key_reports_tip() {
    let home = home();
    hashcli(home.path())
        .args(["list", "files"])
        .assert()
        .code(1)
        .stderr(predicate::str::contains("No API key configured for google"))
        .stdout(predicate::str::contains("Tip: Run `hashcli --config` to get started."));
}

#[cfg(unix)]
#[test]
fn plugin_lifecycle() {
    let home = home();
    let src = tempfile::tempdir().unwrap();
    let script = src.path().join("greet.sh");
    std::fs::write(
        &script,
        "#!/bin/sh\nif [ \"$1\" = \"--hashcli-help\" ]; then echo 'Greet someone'; exit 0; fi\necho \"Hi, $*!\"\n",
    )
    .unwrap();

    hashcli(home.path())
        .args(["--add-cmd", script.to_str().unwrap()])
        .assert()
        .code(1)
        .stdout(predicate::str::contains(
            "Refusing to install plugin non-interactively without --yes.",
        ));

    hashcli(home.path())
        .args(["--add-cmd", script.to_str().unwrap(), "--yes"])
        .assert()
        .success()
        .stdout(
            predicate::str::contains("Installed plugin:")
                .and(predicate::str::contains("Available slash command: /greet")),
        );

    hashcli(home.path())
        .args(["-q", "/greet", "there"])
        .assert()
        .success()
        .stdout("Hi, there!\n");
    hashcli(home.path())
        .arg("--list-plugins")
        .assert()
        .success()
        .stdout(predicate::str::is_match(r"^/greet\t.*greet\.sh\tGreet someone\n$").unwrap());
    hashcli(home.path())
        .arg("--completion-commands")
        .assert()
        .success()
        .stdout(predicate::str::contains("greet\tGreet someone\n").and(predicate::str::contains("tldr\t")));
    hashcli(home.path())
        .args(["--remove-cmd", "/greet", "--yes"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Removed plugin:"));
    hashcli(home.path())
        .args(["-q", "/greet"])
        .assert()
        .stdout(predicate::str::contains("Unknown command: /greet"));
}

#[cfg(unix)]
#[test]
fn example_python_plugin_installs() {
    if which::which("python3").is_err() {
        return;
    }
    let home = home();
    let example = Path::new(env!("CARGO_MANIFEST_DIR")).join("plugins/hello.py");
    hashcli(home.path())
        .args(["--add-cmd", example.to_str().unwrap(), "-y"])
        .assert()
        .success();
    hashcli(home.path())
        .args(["-q", "/hello", "world"])
        .assert()
        .success()
        .stdout("Hello, world!\n");
}

/// Replays queued JSON responses, one per request.
struct Sequence(Mutex<VecDeque<Value>>);

impl Sequence {
    fn new(responses: Vec<Value>) -> Self {
        Sequence(Mutex::new(responses.into()))
    }
}

impl Respond for Sequence {
    fn respond(&self, _request: &Request) -> ResponseTemplate {
        let body = self
            .0
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or_else(|| text_output("exhausted"));
        ResponseTemplate::new(200).set_body_json(body)
    }
}

fn text_output(text: &str) -> Value {
    json!({"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]})
}

async fn openai_mock(responses: Vec<Value>) -> MockServer {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/responses"))
        .respond_with(Sequence::new(responses))
        .mount(&server)
        .await;
    server
}

fn openai_env(cmd: &mut assert_cmd::Command, server: &MockServer) {
    cmd.env("HASHCLI_LLM_PROVIDER", "openai")
        .env("HASHCLI_OPENAI_API_KEY", "sk-test")
        .env("HASHCLI_OPENAI_BASE_URL", server.uri());
}

#[cfg(unix)]
#[tokio::test(flavor = "multi_thread")]
async fn llm_mode_executes_tool_calls_and_saves_history() {
    let server = openai_mock(vec![
        json!({"output": [{"type": "function_call", "call_id": "c1", "name": "execute_shell_command",
                            "arguments": "{\"command\":\"echo e2e-output\",\"description\":\"echo\"}"}]}),
        text_output("The command printed e2e-output."),
    ])
    .await;
    let home = home();
    let mut cmd = hashcli(home.path());
    openai_env(&mut cmd, &server);
    cmd.args(["-q", "-n", "run echo e2e-output"]);
    let assert = tokio::task::spawn_blocking(move || cmd.assert()).await.unwrap();
    assert.success().stdout("The command printed e2e-output.\n");

    let requests = server.received_requests().await.unwrap();
    assert_eq!(requests.len(), 2);
    let follow_up: Value = serde_json::from_slice(&requests[1].body).unwrap();
    let items = follow_up["input"].as_array().unwrap();
    assert!(
        items
            .iter()
            .any(|i| i["type"] == "function_call_output" && i["output"] == "e2e-output")
    );
    assert!(home.path().join(".hashcli/history/conversations.db").exists());
}

#[tokio::test(flavor = "multi_thread")]
async fn how_to_suggestion_is_not_run_without_a_terminal() {
    let server = openai_mock(vec![text_output("Use du to see sizes.\nSUGGESTED_COMMAND: du -sh *")]).await;
    let home = home();
    let mut cmd = hashcli(home.path());
    openai_env(&mut cmd, &server);
    cmd.args(["-q", "# how do I see folder sizes"]);
    let assert = tokio::task::spawn_blocking(move || cmd.assert()).await.unwrap();
    assert
        .success()
        .stdout("Use du to see sizes.\nSuggested command requires confirmation; skipping in non-interactive mode.\n");

    let requests = server.received_requests().await.unwrap();
    let body: Value = serde_json::from_slice(&requests[0].body).unwrap();
    let tool_names: Vec<&str> = body["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| t["name"].as_str().unwrap())
        .collect();
    assert_eq!(tool_names, vec!["lookup_tldr_command"]);
    assert_eq!(body["input"][0]["content"][0]["text"], "how do I see folder sizes");
}

#[cfg(unix)]
#[tokio::test(flavor = "multi_thread")]
async fn embedded_hint_runs_suggested_command() {
    let server = openai_mock(vec![text_output("SUGGESTED_COMMAND: echo hint-ok")]).await;
    let home = home();
    let mut cmd = hashcli(home.path());
    openai_env(&mut cmd, &server);
    cmd.args(["-q", "echo # print hint-ok"]);
    let assert = tokio::task::spawn_blocking(move || cmd.assert()).await.unwrap();
    assert.success().stdout("hint-ok\n");

    let requests = server.received_requests().await.unwrap();
    let body: Value = serde_json::from_slice(&requests[0].body).unwrap();
    assert_eq!(
        body["input"][0]["content"][0]["text"],
        "Task: print hint-ok. Use `echo` as command hint. Execute a single concrete shell command now and return the command result."
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn streaming_output_hides_execute_lines() {
    let server = MockServer::start().await;
    let events = [
        json!({"type": "response.output_text.delta", "delta": "Line one\nSUGGESTED"}),
        json!({"type": "response.output_text.delta", "delta": "_COMMAND: ls\nLine two"}),
        json!({"type": "response.completed", "response": text_output("Line one\nSUGGESTED_COMMAND: ls\nLine two")}),
    ];
    let body: String = events.iter().map(|e| format!("data: {e}\n\n")).collect();
    Mock::given(method("POST"))
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("content-type", "text/event-stream")
                .set_body_string(body),
        )
        .mount(&server)
        .await;
    let home = home();
    let mut cmd = hashcli(home.path());
    openai_env(&mut cmd, &server);
    cmd.env("HASHCLI_STREAMING", "true").args(["-q", "tell me a story"]);
    let assert = tokio::task::spawn_blocking(move || cmd.assert()).await.unwrap();
    assert.success().stdout("Line one\nLine two\n\n");
}

#[tokio::test(flavor = "multi_thread")]
async fn scripted_setup_wizard() {
    let server = openai_mock(vec![text_output("ok")]).await;
    let home = home();
    let mut cmd = hashcli(home.path());
    cmd.env("OPENAI_BASE_URL", server.uri())
        .arg("--config")
        .write_stdin("1\n2\nsk-scripted\nn\n");
    let assert = tokio::task::spawn_blocking(move || cmd.assert()).await.unwrap();
    assert.success().stdout(
        predicate::str::contains("Openai selected")
            .and(predicate::str::contains("Provider validation succeeded."))
            .and(predicate::str::contains("Shell integration was skipped"))
            .and(predicate::str::contains("Setup complete!")),
    );
    let config: toml::Table = std::fs::read_to_string(home.path().join(".hashcli/config.toml"))
        .unwrap()
        .parse()
        .unwrap();
    assert_eq!(config["llm_provider"].as_str(), Some("openai"));
    assert_eq!(config["openai_model"].as_str(), Some("gpt-5.1-codex-mini"));
    assert_eq!(config["openai_api_key"].as_str(), Some("sk-scripted"));
    assert_eq!(config["streaming"].as_bool(), Some(false));

    let requests = server.received_requests().await.unwrap();
    let body: Value = serde_json::from_slice(&requests[0].body).unwrap();
    assert_eq!(body["instructions"], "Reply with exactly: ok");
    assert_eq!(body["model"], "gpt-5.1-codex-mini");
}

#[test]
fn wizard_rejects_invalid_provider() {
    let home = home();
    hashcli(home.path())
        .arg("--config")
        .write_stdin("7\n")
        .assert()
        .code(1)
        .stdout(predicate::str::contains("Invalid choice"));
}
