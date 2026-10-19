//! Executable slash-command plugins installed in `~/.hashcli/plugins/`.
//!
//! Protocol:
//! - `<plugin> <args...>`: stdout becomes the command result; stdin/stderr are inherited.
//!   `HASHCLI_PLUGIN_NAME` and `HASHCLI_PLUGIN_CONFIG` (JSON, API keys removed) are set.
//! - `<plugin> --hashcli-help`: print help text (first line is the summary) and exit 0.
//!
//! The command name is the lowercase file stem (`hello.py` → `/hello`).

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use async_trait::async_trait;

use crate::commands::{BUILTIN_COMMANDS, Command};
use crate::config::{HashConfig, hashcli_home};
use crate::ui;

pub const HELP_FLAG: &str = "--hashcli-help";
const HELP_TIMEOUT: Duration = Duration::from_secs(5);
const SCRIPT_EXTENSIONS: &[&str] = &[
    "py", "sh", "bash", "zsh", "fish", "ps1", "rb", "pl", "js", "exe", "cmd", "bat",
];

pub fn plugin_directory() -> PathBuf {
    hashcli_home().join("plugins")
}

#[derive(Debug, Clone, PartialEq)]
pub struct PluginInfo {
    pub name: String,
    pub path: PathBuf,
}

fn command_name(path: &Path) -> Option<String> {
    path.file_stem().map(|s| s.to_string_lossy().to_lowercase())
}

fn is_hidden_or_private(path: &Path) -> bool {
    path.file_name()
        .map(|n| n.to_string_lossy())
        .is_some_and(|n| n.starts_with('_') || n.starts_with('.'))
}

#[cfg(unix)]
fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn is_executable(path: &Path) -> bool {
    path.is_file()
}

fn has_script_extension(path: &Path) -> bool {
    path.extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .is_some_and(|e| SCRIPT_EXTENSIONS.contains(&e.as_str()))
}

/// Installed plugin files that can be run, sorted by file name.
pub fn discover_plugins() -> Vec<PluginInfo> {
    let mut plugins: Vec<PluginInfo> = Vec::new();
    let Ok(entries) = std::fs::read_dir(plugin_directory()) else {
        return plugins;
    };
    let mut paths: Vec<PathBuf> = entries.filter_map(Result::ok).map(|e| e.path()).collect();
    paths.sort();
    for path in paths {
        if is_hidden_or_private(&path) || !is_executable(&path) {
            continue;
        }
        let Some(name) = command_name(&path) else {
            continue;
        };
        if plugins.iter().any(|p| p.name == name) {
            continue;
        }
        plugins.push(PluginInfo { name, path });
    }
    plugins
}

/// Program and leading arguments used to run a plugin file.
fn launcher(path: &Path) -> (PathBuf, Vec<String>) {
    if cfg!(windows) {
        let ext = path
            .extension()
            .map(|e| e.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        let file = path.to_string_lossy().into_owned();
        match ext.as_str() {
            "py" => return (PathBuf::from("python"), vec![file]),
            "ps1" => {
                let shell = which::which("pwsh").unwrap_or_else(|_| PathBuf::from("powershell"));
                return (
                    shell,
                    vec![
                        "-NoProfile".into(),
                        "-ExecutionPolicy".into(),
                        "Bypass".into(),
                        "-File".into(),
                        file,
                    ],
                );
            }
            _ => {}
        }
    }
    (path.to_path_buf(), Vec::new())
}

fn std_command(path: &Path) -> std::process::Command {
    let (program, prefix) = launcher(path);
    let mut cmd = std::process::Command::new(program);
    cmd.args(prefix);
    cmd
}

/// Run `<plugin> --hashcli-help` with a timeout. Returns stdout on exit code 0.
pub fn probe_help(path: &Path) -> Result<String> {
    let mut child = std_command(path)
        .arg(HELP_FLAG)
        .env("HASHCLI_PLUGIN_NAME", command_name(path).unwrap_or_default())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .with_context(|| format!("failed to run {}", path.display()))?;

    let mut stdout = child.stdout.take().expect("piped stdout");
    let mut stderr = child.stderr.take().expect("piped stderr");
    let out_reader = std::thread::spawn(move || {
        let mut buf = String::new();
        let _ = stdout.read_to_string(&mut buf);
        buf
    });
    let err_reader = std::thread::spawn(move || {
        let mut buf = String::new();
        let _ = stderr.read_to_string(&mut buf);
        buf
    });

    let deadline = Instant::now() + HELP_TIMEOUT;
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            bail!("`{} {HELP_FLAG}` timed out", path.display());
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let out = out_reader.join().unwrap_or_default();
    let err = err_reader.join().unwrap_or_default();
    if !status.success() {
        let details = if err.trim().is_empty() { out.trim() } else { err.trim() };
        bail!(
            "`{} {HELP_FLAG}` exited with {}{}",
            path.display(),
            status
                .code()
                .map_or_else(|| "a signal".to_string(), |c| format!("code {c}")),
            if details.is_empty() {
                String::new()
            } else {
                format!(": {details}")
            }
        );
    }
    Ok(out.trim_end().to_string())
}

fn first_line(text: &str) -> &str {
    text.lines().next().unwrap_or("")
}

pub struct PluginCommand {
    info: PluginInfo,
}

impl PluginCommand {
    pub fn new(info: PluginInfo) -> Self {
        PluginCommand { info }
    }
}

#[async_trait(?Send)]
impl Command for PluginCommand {
    async fn execute(&self, args: &[String], config: &HashConfig) -> Result<String> {
        let (program, prefix) = launcher(&self.info.path);
        let output = tokio::process::Command::new(program)
            .args(prefix)
            .args(args)
            .env("HASHCLI_PLUGIN_NAME", &self.info.name)
            .env("HASHCLI_PLUGIN_CONFIG", config.to_plugin_json())
            .stdin(Stdio::inherit())
            .stderr(Stdio::inherit())
            .stdout(Stdio::piped())
            .output()
            .await
            .with_context(|| format!("failed to run plugin {}", self.info.path.display()))?;
        let stdout = String::from_utf8_lossy(&output.stdout).trim_end().to_string();
        if !output.status.success() {
            let code = output
                .status
                .code()
                .map_or_else(|| "signal".to_string(), |c| c.to_string());
            if stdout.is_empty() {
                bail!("exit code {code}");
            }
            bail!("exit code {code}\n{stdout}");
        }
        Ok(stdout)
    }

    fn help(&self) -> String {
        match probe_help(&self.info.path) {
            Ok(help) if !help.trim().is_empty() => help,
            _ => "No help available.".to_string(),
        }
    }
}

/// Resolve `--add-cmd` input (a file, or a directory with one plugin) to a plugin file.
pub fn resolve_add_cmd_source(value: &str) -> Result<PathBuf> {
    let source = crate::config::expand_home(Path::new(value));
    if !source.exists() {
        bail!("Plugin source does not exist: {}", source.display());
    }
    if source.is_file() {
        return Ok(std::fs::canonicalize(&source)?);
    }
    if !source.is_dir() {
        bail!("Invalid plugin source path: {}", source.display());
    }

    let mut candidates = Vec::new();
    collect_candidates(&source, &mut candidates)?;
    candidates.sort();
    match candidates.len() {
        0 => bail!("No plugin file found in directory: {}", source.display()),
        1 => return Ok(std::fs::canonicalize(&candidates[0])?),
        _ => {}
    }
    let dir_name = source
        .file_name()
        .map(|n| n.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    let matched: Vec<&PathBuf> = candidates
        .iter()
        .filter(|p| command_name(p).as_deref() == Some(dir_name.as_str()))
        .collect();
    if matched.len() == 1 {
        return Ok(std::fs::canonicalize(matched[0])?);
    }
    let names: Vec<String> = candidates
        .iter()
        .filter_map(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()))
        .collect();
    bail!(
        "Directory contains multiple plugin files. Provide a specific plugin file path. Candidates: {}",
        names.join(", ")
    )
}

fn collect_candidates(dir: &Path, out: &mut Vec<PathBuf>) -> Result<()> {
    for entry in std::fs::read_dir(dir)? {
        let path = entry?.path();
        if is_hidden_or_private(&path) {
            continue;
        }
        if path.is_dir() {
            collect_candidates(&path, out)?;
        } else if path.is_file() && (has_script_extension(&path) || (cfg!(unix) && is_executable(&path))) {
            out.push(path);
        }
    }
    Ok(())
}

fn validate_command_name(name: &str) -> Result<()> {
    if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
        bail!("Invalid plugin command name: /{name}. Use letters, digits, '-' or '_'.");
    }
    if BUILTIN_COMMANDS.contains(&name) {
        bail!("/{name} is a built-in command and cannot be replaced by a plugin.");
    }
    Ok(())
}

fn make_executable(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755))?;
    }
    let _ = path;
    Ok(())
}

/// Plugin files in the plugin directory whose command name is `name`.
fn installed_files_for(name: &str) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(plugin_directory()) else {
        return Vec::new();
    };
    let mut files: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.is_file() && !is_hidden_or_private(p) && command_name(p).as_deref() == Some(name))
        .collect();
    files.sort();
    files
}

#[derive(Debug)]
pub struct InstalledPlugin {
    pub name: String,
    pub path: PathBuf,
}

/// Validate, preview, confirm, and install a plugin. Returns the installed command.
pub fn install_plugin(source_value: &str, yes: bool) -> Result<InstalledPlugin> {
    let source = resolve_add_cmd_source(source_value)?;
    let name = command_name(&source).unwrap_or_default();
    validate_command_name(&name)?;
    let file_name = source.file_name().context("plugin file has no name")?.to_owned();
    let destination = plugin_directory().join(&file_name);

    // Validate an executable copy so plugins without the exec bit still work.
    let staging = tempfile::tempdir()?;
    let staged = staging.path().join(&file_name);
    std::fs::copy(&source, &staged)?;
    make_executable(&staged)?;
    let help = probe_help(&staged).map_err(|e| anyhow::anyhow!("Plugin validation failed before install: {e:#}"))?;

    println!("{}", ui::bold("Plugin preview"));
    println!("Command: {}", ui::cyan(&format!("/{name}")));
    println!("Install path: {}", ui::dim(&destination.display().to_string()));
    let summary = first_line(&help);
    if !summary.is_empty() {
        println!("Help: {summary}");
    }

    if !yes {
        if !ui::is_interactive() {
            bail!("Refusing to install plugin non-interactively without --yes.");
        }
        if !ui::confirm("Install this plugin?", false) {
            bail!("Plugin install cancelled.");
        }
    }

    std::fs::create_dir_all(plugin_directory())?;
    for existing in installed_files_for(&name) {
        if existing != destination {
            std::fs::remove_file(&existing)?;
        }
    }
    std::fs::copy(&staged, &destination)?;
    make_executable(&destination)?;
    Ok(InstalledPlugin {
        name,
        path: destination,
    })
}

#[derive(Debug)]
pub struct PluginListing {
    pub name: String,
    pub path: PathBuf,
    pub help: String,
}

pub fn list_plugins() -> Vec<PluginListing> {
    discover_plugins()
        .into_iter()
        .map(|p| {
            let help = match probe_help(&p.path) {
                Ok(help) => first_line(&help).to_string(),
                Err(e) => format!("<help failed: {e:#}>"),
            };
            PluginListing {
                name: p.name,
                path: p.path,
                help,
            }
        })
        .collect()
}

/// Remove an installed plugin by command name. Returns the removed path.
pub fn remove_plugin(command: &str, yes: bool) -> Result<PathBuf> {
    let normalized = command.trim().trim_start_matches('/').to_lowercase();
    if normalized.is_empty() {
        bail!("Plugin command name is required.");
    }
    let files = installed_files_for(&normalized);
    let Some(first) = files.first().cloned() else {
        bail!("No installed plugin found for /{normalized}.");
    };
    if !yes {
        if !ui::is_interactive() {
            bail!("Refusing to remove plugin non-interactively without --yes.");
        }
        if !ui::confirm(&format!("Remove /{normalized} plugin?"), false) {
            bail!("Plugin removal cancelled.");
        }
    }
    for file in files {
        std::fs::remove_file(file)?;
    }
    Ok(first)
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use crate::commands::CommandProxy;
    use crate::commands::tests::test_config;
    use crate::config::tests::EnvGuard;

    const HELLO: &str = r#"#!/bin/sh
if [ "$1" = "--hashcli-help" ]; then
  printf 'Say hello\n  /hello <name>\n'
  exit 0
fi
if [ $# -gt 0 ]; then echo "Hello, $*!"; else echo "Hello from $HASHCLI_PLUGIN_NAME"; fi
"#;

    fn write_script(dir: &Path, name: &str, body: &str, executable: bool) -> PathBuf {
        let path = dir.join(name);
        std::fs::write(&path, body).unwrap();
        if executable {
            make_executable(&path).unwrap();
        }
        path
    }

    #[tokio::test]
    async fn install_run_list_remove() {
        let env = EnvGuard::new();
        let src = tempfile::tempdir().unwrap();
        let script = write_script(src.path(), "hello.sh", HELLO, false);

        let installed = install_plugin(script.to_str().unwrap(), true).unwrap();
        assert_eq!(installed.name, "hello");
        assert!(is_executable(&installed.path));

        let cfg = test_config(&env);
        let proxy = CommandProxy::new(&cfg);
        assert!(proxy.is_plugin("hello"));
        assert_eq!(proxy.execute("/hello world").await, "Hello, world!");
        assert_eq!(proxy.execute("/hello").await, "Hello from hello");
        assert!(proxy.command_help("hello").unwrap().starts_with("Say hello"));
        let help = proxy.execute("/help").await;
        assert!(help.contains("PLUGINS:\n  /hello"));

        let listing = list_plugins();
        assert_eq!(listing.len(), 1);
        assert_eq!(listing[0].help, "Say hello");

        let removed = remove_plugin("/hello", true).unwrap();
        assert_eq!(removed, installed.path);
        assert!(discover_plugins().is_empty());
        assert!(remove_plugin("hello", true).is_err());
    }

    #[tokio::test]
    async fn plugin_failure_is_reported() {
        let env = EnvGuard::new();
        let dir = plugin_directory();
        std::fs::create_dir_all(&dir).unwrap();
        write_script(&dir, "boom.sh", "#!/bin/sh\necho partial\nexit 3\n", true);
        write_script(&dir, "_private.sh", "#!/bin/sh\n", true);
        write_script(&dir, "notexec.sh", "#!/bin/sh\n", false);
        write_script(&dir, "help.sh", "#!/bin/sh\necho hijack\n", true);
        let proxy = CommandProxy::new(&test_config(&env));
        assert_eq!(
            proxy.available_commands(),
            vec!["boom", "config", "help", "history", "tldr"]
        );
        assert_eq!(
            proxy.execute("/boom").await,
            "Command execution error: exit code 3\npartial"
        );
        assert!(proxy.execute("/help").await.starts_with("Hash CLI - Command Mode"));
        assert_eq!(proxy.command_help("boom").unwrap(), "No help available.");
    }

    #[test]
    fn install_rejects_bad_plugins() {
        let _env = EnvGuard::new();
        let src = tempfile::tempdir().unwrap();
        let no_help = write_script(src.path(), "nohelp.sh", "#!/bin/sh\nexit 1\n", true);
        let err = install_plugin(no_help.to_str().unwrap(), true).unwrap_err();
        assert!(
            err.to_string().starts_with("Plugin validation failed before install"),
            "{err}"
        );
        assert!(!plugin_directory().join("nohelp.sh").exists());

        let builtin = write_script(src.path(), "config.sh", HELLO, true);
        let err = install_plugin(builtin.to_str().unwrap(), true).unwrap_err();
        assert!(err.to_string().contains("built-in command"));

        let ok = write_script(src.path(), "greet.sh", HELLO, true);
        let err = install_plugin(ok.to_str().unwrap(), false).unwrap_err();
        assert_eq!(
            err.to_string(),
            "Refusing to install plugin non-interactively without --yes."
        );
        assert!(install_plugin("/does/not/exist", true).is_err());
    }

    #[test]
    fn resolves_directory_sources() {
        let _env = EnvGuard::new();
        let root = tempfile::tempdir().unwrap();
        let single = root.path().join("single");
        std::fs::create_dir_all(&single).unwrap();
        write_script(&single, "only.py", "print()", false);
        std::fs::write(single.join("README.md"), "docs").unwrap();
        assert!(
            resolve_add_cmd_source(single.to_str().unwrap())
                .unwrap()
                .ends_with("only.py")
        );

        let multi = root.path().join("tool");
        std::fs::create_dir_all(&multi).unwrap();
        write_script(&multi, "tool.sh", "", false);
        write_script(&multi, "other.sh", "", false);
        assert!(
            resolve_add_cmd_source(multi.to_str().unwrap())
                .unwrap()
                .ends_with("tool.sh")
        );

        let ambiguous = root.path().join("amb");
        std::fs::create_dir_all(&ambiguous).unwrap();
        write_script(&ambiguous, "a.sh", "", false);
        write_script(&ambiguous, "b.sh", "", false);
        let err = resolve_add_cmd_source(ambiguous.to_str().unwrap()).unwrap_err();
        assert!(err.to_string().contains("Candidates: a.sh, b.sh"));

        let empty = root.path().join("empty");
        std::fs::create_dir_all(&empty).unwrap();
        assert!(resolve_add_cmd_source(empty.to_str().unwrap()).is_err());
    }
}
