//! Tools the LLM can call.

pub mod shell;

use async_trait::async_trait;
use serde_json::Value;

use crate::config::HashConfig;

pub use shell::ShellTool;

#[async_trait(?Send)]
pub trait Tool {
    fn name(&self) -> &'static str;

    /// Execute with LLM-provided arguments. Failures are reported as text, not errors,
    /// unless something unexpected happens.
    async fn execute(&self, arguments: &Value, config: &HashConfig) -> anyhow::Result<String>;

    /// Whether config-level confirmation applies to this tool.
    fn requires_confirmation(&self) -> bool {
        true
    }
}

pub fn get_tool_executor(name: &str) -> Option<Box<dyn Tool>> {
    match name {
        "execute_shell_command" => Some(Box::new(ShellTool)),
        "lookup_tldr_command" => Some(Box::new(crate::tldr::TldrTool)),
        _ => None,
    }
}
