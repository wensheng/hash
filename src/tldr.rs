//! Native tldr client used for command grounding (`lookup_tldr_command`) and `/tldr`.
//!
//! Ported from the embedded tldr-python-client: same cache layout (`~/.cache/tldr`),
//! `TLDR_*` environment settings, page priority, and rendering.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;
use std::time::{Duration, SystemTime};

use async_trait::async_trait;
use clap::Parser;
use percent_encoding::{AsciiSet, NON_ALPHANUMERIC, utf8_percent_encode};
use regex::Regex;
use serde_json::Value;

use crate::commands::Command;
use crate::config::HashConfig;
use crate::shlex;
use crate::tools::Tool;

const CLIENT_SPECIFICATION: &str = "2.3";
const DEFAULT_PAGES_SOURCE: &str = "https://raw.githubusercontent.com/tldr-pages/tldr/main/pages";
const DEFAULT_DOWNLOAD_CACHE: &str = "https://github.com/tldr-pages/tldr/releases/latest/download/tldr.zip";
const LEADING_SPACES: usize = 2;

/// sys.platform / OS name → tldr pages directory.
const OS_DIRECTORIES: &[(&str, &str)] = &[
    ("android", "android"),
    ("darwin", "osx"),
    ("freebsd", "freebsd"),
    ("linux", "linux"),
    ("macos", "osx"),
    ("netbsd", "netbsd"),
    ("openbsd", "openbsd"),
    ("osx", "osx"),
    ("sunos", "sunos"),
    ("win32", "windows"),
    ("windows", "windows"),
];

const ALL_PLATFORMS: &[&str] = &[
    "android", "freebsd", "linux", "netbsd", "openbsd", "osx", "sunos", "windows", "common",
];

/// Like Python's `urllib.parse.quote` with `safe='/'`.
const PATH_SEGMENT: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'_')
    .remove(b'.')
    .remove(b'-')
    .remove(b'~')
    .remove(b'/');

static EXAMPLE_SPLIT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"`.+?`").unwrap());
static EXAMPLE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"`(.+?)`").unwrap());
static COMMAND_SPLIT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{\{.+?\}*\}\}").unwrap());
static PARAM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{\{(.+?)\}\}").unwrap());
static SHORT_OPTION: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{\{\[([^|]+)\|[^|]+?\]\}\}").unwrap());
static LONG_OPTION: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{\{\[[^|]+\|([^|]+?)\]\}\}").unwrap());
static ZIP_ENTRY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(.+)/(.+)\.md").unwrap());

pub fn os_directory(name: &str) -> Option<&'static str> {
    OS_DIRECTORIES.iter().find(|(k, _)| *k == name).map(|(_, v)| *v)
}

/// tldr directory for the platform this binary runs on.
pub fn current_platform() -> &'static str {
    os_directory(std::env::consts::OS).unwrap_or("linux")
}

pub fn get_language_code(language: &str) -> String {
    let language = language.split('.').next().unwrap_or("");
    if matches!(language, "pt_PT" | "pt_BR" | "zh_TW") {
        return language.to_string();
    }
    if language == "pt" {
        return "pt_PT".to_string();
    }
    language.split('_').next().unwrap_or("").to_string()
}

/// Language priority from `TLDR_LANGUAGE`, `LANGUAGE`, and `LANG`.
pub fn language_list(tldr_language: &str, language_env: &str, lang_env: &str) -> Vec<String> {
    let tldr_language = get_language_code(tldr_language);
    let mut languages: Vec<String> = language_env
        .split(':')
        .filter(|l| !matches!(*l, "C" | "POSIX" | ""))
        .map(get_language_code)
        .collect();
    let default_lang = get_language_code(if lang_env.is_empty() { "C" } else { lang_env });
    if default_lang == "C" || default_lang == "POSIX" {
        languages.clear();
    } else if !languages.contains(&default_lang) {
        languages.push(default_lang);
    }
    if !tldr_language.is_empty() {
        languages.retain(|l| *l != tldr_language);
        languages.insert(0, tldr_language);
    }
    if !languages.iter().any(|l| l == "en") {
        languages.push("en".to_string());
    }
    languages
}

fn pages_dir(language: &str) -> String {
    if language.is_empty() || language == "en" {
        "pages".to_string()
    } else {
        format!("pages.{language}")
    }
}

fn env_flag(name: &str, default: i64) -> i64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(default)
}

#[derive(Debug)]
enum FetchError {
    Http(u16),
    Url(String),
}

impl std::fmt::Display for FetchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FetchError::Http(code) => write!(f, "HTTP Error {code}"),
            FetchError::Url(reason) => write!(f, "<urlopen error {reason}>"),
        }
    }
}

#[derive(Debug)]
enum PageError {
    CacheNotExist,
    Fetch(FetchError),
}

/// Text style configured via `TLDR_COLOR_<KEY>` (termcolor names).
#[derive(Debug, Default, Clone)]
struct ColorSpec {
    codes: Vec<u8>,
}

fn color_code(name: &str) -> Option<u8> {
    Some(match name {
        "grey" => 30,
        "red" => 31,
        "green" => 32,
        "yellow" => 33,
        "blue" => 34,
        "magenta" => 35,
        "cyan" => 36,
        "white" => 97,
        _ => return None,
    })
}

fn background_code(name: &str) -> Option<u8> {
    Some(match name {
        "on_grey" => 40,
        "on_red" => 41,
        "on_green" => 42,
        "on_yellow" => 43,
        "on_blue" => 44,
        "on_magenta" => 45,
        "on_cyan" => 46,
        "on_white" => 107,
        _ => return None,
    })
}

fn attr_code(name: &str) -> Option<u8> {
    Some(match name {
        "bold" => 1,
        "dark" => 2,
        "underline" => 4,
        "blink" => 5,
        "reverse" => 7,
        "concealed" => 8,
        _ => return None,
    })
}

impl ColorSpec {
    fn parse(spec: &str) -> Self {
        let mut color = None;
        let mut background = None;
        let mut attrs = Vec::new();
        for value in spec.split_whitespace() {
            if let Some(c) = color_code(value) {
                color = Some(c);
            } else if let Some(b) = background_code(value) {
                background = Some(b);
            } else if let Some(a) = attr_code(value) {
                attrs.push(a);
            }
        }
        let mut codes: Vec<u8> = color.into_iter().chain(background).collect();
        codes.extend(attrs);
        ColorSpec { codes }
    }
}

/// Split keeping the matched separators, like Python's `re.split` with a capture group.
fn split_keep<'a>(re: &Regex, text: &'a str) -> Vec<&'a str> {
    let mut parts = Vec::new();
    let mut last = 0;
    for m in re.find_iter(text) {
        parts.push(&text[last..m.start()]);
        parts.push(m.as_str());
        last = m.end();
    }
    parts.push(&text[last..]);
    parts.into_iter().filter(|p| !p.is_empty()).collect()
}

#[derive(Parser, Debug)]
#[command(
    name = "tldr",
    override_usage = "tldr command [options]",
    about = "Command line client for tldr pages",
    disable_version_flag = true
)]
struct TldrArgs {
    /// Show version and exit
    #[arg(short = 'v', long = "version")]
    version: bool,
    /// Search for a specific command from a query
    #[arg(long, value_name = "\"KEYWORDS\"")]
    search: Option<String>,
    /// Update the local cache of pages and exit
    #[arg(short = 'u', long = "update", alias = "update_cache")]
    update: bool,
    /// Delete the local cache of pages and exit
    #[arg(short = 'k', long = "clear-cache")]
    clear_cache: bool,
    /// Override the operating system
    #[arg(short = 'p', long, value_name = "PLATFORM", value_parser = clap::builder::PossibleValuesParser::new(ALL_PLATFORMS))]
    platform: Option<String>,
    /// List all available commands for operating system
    #[arg(short = 'l', long)]
    list: bool,
    /// Override the default page source
    #[arg(short = 's', long)]
    source: Option<String>,
    /// Override color stripping
    #[arg(short = 'c', long)]
    color: bool,
    /// Render local markdown files
    #[arg(short = 'r', long)]
    render: bool,
    /// Override the default language
    #[arg(short = 'L', long)]
    language: Option<String>,
    /// Just print the plain page file
    #[arg(short = 'm', long)]
    markdown: bool,
    /// Display shortform options over longform
    #[arg(long)]
    short_options: bool,
    /// Display longform options over shortform
    #[arg(long)]
    long_options: bool,
    /// Command to look up
    #[arg(value_name = "command")]
    command: Vec<String>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum OptionLength {
    Short,
    Long,
    Both,
}

/// tldr client state, normally built from the environment.
pub struct Tldr {
    pub pages_source: String,
    pub download_cache: String,
    pub use_cache: bool,
    pub max_cache_age_hours: f64,
    pub cache_dir: PathBuf,
    pub system_cache_dir: PathBuf,
    pub languages: Vec<String>,
    pub platform: String,
    pub platform_env: String,
    pub options_env: String,
    pub force_color: bool,
    pub no_color: bool,
    pub color_overrides: HashMap<String, String>,
    pub cert: Option<PathBuf>,
    pub allow_insecure: bool,
}

impl Tldr {
    pub fn from_env() -> Self {
        let env = |name: &str| std::env::var(name).unwrap_or_default();
        let cache_dir = if !env("XDG_CACHE_HOME").is_empty() {
            PathBuf::from(env("XDG_CACHE_HOME")).join("tldr")
        } else if !env("HOME").is_empty() {
            PathBuf::from(env("HOME")).join(".cache").join("tldr")
        } else {
            crate::config::home_dir().join(".cache").join("tldr")
        };
        let system_cache_dir = env("XDG_DATA_DIRS")
            .split(':')
            .filter(|e| !e.is_empty())
            .map(|e| Path::new(e).join("tldr"))
            .find(|p| p.is_dir())
            .unwrap_or_else(|| PathBuf::from("/usr/share/tldr"));
        let color_overrides = ["name", "description", "example", "command", "parameter"]
            .iter()
            .filter_map(|key| {
                std::env::var(format!("TLDR_COLOR_{}", key.to_uppercase()))
                    .ok()
                    .map(|v| (key.to_string(), v))
            })
            .collect();
        let pages_source = std::env::var("TLDR_PAGES_SOURCE_LOCATION")
            .unwrap_or_else(|_| DEFAULT_PAGES_SOURCE.into())
            .trim_end_matches('/')
            .to_string();
        Tldr {
            pages_source,
            download_cache: std::env::var("TLDR_DOWNLOAD_CACHE_LOCATION")
                .unwrap_or_else(|_| DEFAULT_DOWNLOAD_CACHE.into()),
            use_cache: env_flag("TLDR_CACHE_ENABLED", 1) > 0,
            max_cache_age_hours: env_flag("TLDR_CACHE_MAX_AGE", 24 * 7) as f64,
            cache_dir,
            system_cache_dir,
            languages: language_list(&env("TLDR_LANGUAGE"), &env("LANGUAGE"), &env("LANG")),
            platform: current_platform().to_string(),
            platform_env: env("TLDR_PLATFORM").trim().to_lowercase(),
            options_env: env("TLDR_OPTIONS"),
            force_color: std::env::var_os("FORCE_COLOR").is_some(),
            no_color: std::env::var_os("NO_COLOR").is_some() || std::env::var_os("ANSI_COLORS_DISABLED").is_some(),
            color_overrides,
            cert: std::env::var_os("TLDR_CERT").map(|p| crate::config::expand_home(Path::new(&p))),
            allow_insecure: env_flag("TLDR_ALLOW_INSECURE", 0) == 1,
        }
    }

    fn platform_list(&self) -> Vec<String> {
        let mut platforms = vec![self.platform.clone(), "common".to_string()];
        for p in ALL_PLATFORMS {
            if !platforms.iter().any(|x| x == p) {
                platforms.push(p.to_string());
            }
        }
        platforms
    }

    fn cache_file_path(&self, command: &str, platform: &str, language: &str, system: bool) -> PathBuf {
        let base = if system {
            &self.system_cache_dir
        } else {
            &self.cache_dir
        };
        base.join(pages_dir(language))
            .join(platform)
            .join(format!("{command}.md"))
    }

    fn load_from_cache(&self, command: &str, platform: &str, language: &str, system: bool) -> Option<Vec<u8>> {
        std::fs::read(self.cache_file_path(command, platform, language, system)).ok()
    }

    fn store_to_cache(&self, page: &[u8], command: &str, platform: &str, language: &str) {
        let path = self.cache_file_path(command, platform, language, false);
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::write(path, page);
    }

    fn have_recent_cache(&self, command: &str, platform: &str, language: &str) -> bool {
        let path = self.cache_file_path(command, platform, language, false);
        let Ok(modified) = std::fs::metadata(&path).and_then(|m| m.modified()) else {
            return false;
        };
        let age = SystemTime::now().duration_since(modified).unwrap_or_default();
        age.as_secs_f64() / 3600.0 <= self.max_cache_age_hours
    }

    fn page_url(&self, command: &str, platform: &str, remote: &str, language: &str) -> String {
        let language = if language.is_empty() || language == "en" {
            String::new()
        } else {
            format!(".{language}")
        };
        format!(
            "{remote}{language}/{platform}/{}.md",
            utf8_percent_encode(command, PATH_SEGMENT)
        )
    }

    fn http_client(&self) -> Result<reqwest::Client, FetchError> {
        crate::providers::install_crypto_provider();
        let mut builder = reqwest::Client::builder()
            .user_agent(concat!("hashcli/", env!("CARGO_PKG_VERSION")))
            .timeout(Duration::from_secs(10));
        if self.allow_insecure {
            builder = builder.tls_danger_accept_invalid_certs(true);
        } else if let Some(cert) = &self.cert {
            let pem = std::fs::read(cert).map_err(|e| FetchError::Url(e.to_string()))?;
            let certs = reqwest::Certificate::from_pem_bundle(&pem).map_err(|e| FetchError::Url(e.to_string()))?;
            builder = builder.tls_certs_only(certs);
        }
        builder.build().map_err(|e| FetchError::Url(e.to_string()))
    }

    async fn fetch(&self, url: &str) -> Result<Vec<u8>, FetchError> {
        if let Some(path) = url.strip_prefix("file://") {
            return std::fs::read(path).map_err(|e| FetchError::Url(e.to_string()));
        }
        let response = self
            .http_client()?
            .get(url)
            .send()
            .await
            .map_err(|e| FetchError::Url(e.to_string()))?;
        let status = response.status();
        if !status.is_success() {
            return Err(FetchError::Http(status.as_u16()));
        }
        response
            .bytes()
            .await
            .map(|b| b.to_vec())
            .map_err(|e| FetchError::Url(e.to_string()))
    }

    async fn get_page_for_platform(
        &self,
        command: &str,
        platform: &str,
        remote: &str,
        language: &str,
        only_use_cache: bool,
        system_cache: bool,
    ) -> Result<Vec<String>, PageError> {
        let mut downloaded = false;
        let data =
            if self.use_cache && system_cache && self.cache_file_path(command, platform, language, true).is_file() {
                self.load_from_cache(command, platform, language, true)
            } else if self.use_cache && self.have_recent_cache(command, platform, language) {
                self.load_from_cache(command, platform, language, false)
            } else if only_use_cache {
                return Err(PageError::CacheNotExist);
            } else {
                let url = self.page_url(command, platform, remote, language);
                match self.fetch(&url).await {
                    Ok(bytes) => {
                        downloaded = true;
                        Some(bytes)
                    }
                    Err(e) => {
                        if !self.use_cache {
                            return Err(PageError::Fetch(e));
                        }
                        match self.load_from_cache(command, platform, language, false) {
                            Some(bytes) => Some(bytes),
                            None => return Err(PageError::Fetch(e)),
                        }
                    }
                }
            };
        let Some(data) = data else {
            return Err(PageError::CacheNotExist);
        };
        if downloaded && self.use_cache {
            self.store_to_cache(&data, command, platform, language);
        }
        Ok(String::from_utf8_lossy(&data).lines().map(str::to_string).collect())
    }

    /// Pages for every platform that has one, ordered by priority.
    async fn get_page_for_every_platform(
        &self,
        command: &str,
        remote: &str,
        platforms: Option<Vec<String>>,
        languages: Option<Vec<String>>,
    ) -> Result<Vec<(Vec<String>, String)>, FetchError> {
        let platforms = match platforms {
            None => self.platform_list(),
            Some(mut list) => {
                if !list.is_empty() && !list.iter().any(|p| p == "common") {
                    list.push("common".to_string());
                }
                list
            }
        };
        let languages = languages.unwrap_or_else(|| self.languages.clone());

        if self.use_cache {
            for system_cache in [false, true] {
                let mut result = Vec::new();
                for platform in &platforms {
                    for language in &languages {
                        if let Ok(page) = self
                            .get_page_for_platform(command, platform, remote, language, true, system_cache)
                            .await
                        {
                            result.push((page, platform.clone()));
                            break;
                        }
                    }
                }
                if !result.is_empty() {
                    return Ok(result);
                }
            }
        }

        let mut result = Vec::new();
        let mut error = None;
        for platform in &platforms {
            for language in &languages {
                match self
                    .get_page_for_platform(command, platform, remote, language, false, false)
                    .await
                {
                    Ok(page) => {
                        result.push((page, platform.clone()));
                        break;
                    }
                    Err(PageError::Fetch(FetchError::Http(code))) => {
                        if code != 404 {
                            error = Some(FetchError::Http(code));
                        }
                    }
                    Err(PageError::Fetch(FetchError::Url(reason))) => {
                        if !remote.starts_with("file://") {
                            error = Some(FetchError::Url(reason));
                        }
                    }
                    Err(PageError::CacheNotExist) => {}
                }
            }
        }
        if !result.is_empty() {
            return Ok(result);
        }
        match error {
            Some(e) => Err(e),
            None => Ok(Vec::new()),
        }
    }

    fn get_commands(&self, platforms: Option<Vec<String>>, language: Option<&str>) -> Vec<String> {
        let platforms = platforms.unwrap_or_else(|| self.platform_list());
        let languages = match language {
            Some(l) => vec![get_language_code(l)],
            None => self.languages.clone(),
        };
        let mut commands = Vec::new();
        if !self.cache_dir.exists() {
            return commands;
        }
        for platform in &platforms {
            for language in &languages {
                let path = self.cache_dir.join(pages_dir(language)).join(platform);
                let Ok(entries) = std::fs::read_dir(&path) else {
                    continue;
                };
                let mut names: Vec<String> = entries
                    .filter_map(Result::ok)
                    .map(|e| e.path())
                    .filter(|p| p.extension().is_some_and(|e| e == "md"))
                    .filter_map(|p| p.file_stem().map(|s| s.to_string_lossy().into_owned()))
                    .collect();
                names.sort();
                commands.extend(names);
            }
        }
        commands
    }

    fn colors_enabled(&self, forced: bool) -> bool {
        !self.no_color && (self.force_color || forced)
    }

    fn color_spec(&self, key: &str) -> ColorSpec {
        let default = match key {
            "name" => "bold",
            "example" => "green",
            "command" => "red",
            _ => "",
        };
        ColorSpec::parse(self.color_overrides.get(key).map(String::as_str).unwrap_or(default))
    }

    /// Render page lines (`output()` in the Python client).
    fn render(&self, page: &[String], option_length: OptionLength, plain: bool, color: bool) -> String {
        let colored = |text: &str, key: &str| -> String {
            let spec = self.color_spec(key);
            if !color || spec.codes.is_empty() {
                return text.to_string();
            }
            let prefix: String = spec.codes.iter().map(|c| format!("\x1b[{c}m")).collect();
            format!("{prefix}{text}\x1b[0m")
        };
        let emphasise = |text: &str| -> String {
            if color {
                format!("\x1b[3m\x1b[33m{text}\x1b[0m\x1b[23m")
            } else {
                text.to_string()
            }
        };
        let indent = " ".repeat(LEADING_SPACES);
        let mut out = String::new();
        if !plain {
            out.push('\n');
        }
        for raw in page {
            let line = raw.trim_end();
            if plain {
                out.push_str(line);
                out.push('\n');
                continue;
            }
            if line.is_empty() {
                continue;
            }
            if line.starts_with('#') {
                out.push_str(&indent);
                out.push_str(&colored(&line.replace("# ", ""), "name"));
                out.push('\n');
            } else if line.starts_with('>') {
                out.push_str(&" ".repeat(LEADING_SPACES - 1));
                out.push_str(&colored(&line.replace(['>', '<'], ""), "description"));
            } else if line.starts_with('-') {
                out.push('\n');
                out.push_str(&indent);
                if line.contains('`') {
                    for item in split_keep(&EXAMPLE_SPLIT, line) {
                        match EXAMPLE.captures(item) {
                            Some(caps) if caps.get(0).is_some_and(|m| m.as_str() == item) => {
                                out.push_str(&emphasise(&caps[1]));
                            }
                            _ => out.push_str(&colored(item, "example")),
                        }
                    }
                } else {
                    out.push_str(&colored(line, "example"));
                }
            } else if let Some(rest) = line.strip_prefix('`') {
                let inner = rest.strip_suffix('`').unwrap_or(rest);
                let mut inner = inner
                    .replace(r"\{\{", "__ESCAPED_OPEN__")
                    .replace(r"\}\}", "__ESCAPED_CLOSE__");
                inner = match option_length {
                    OptionLength::Short => SHORT_OPTION.replace_all(&inner, "$1").into_owned(),
                    OptionLength::Long => LONG_OPTION.replace_all(&inner, "$1").into_owned(),
                    OptionLength::Both => inner,
                };
                let mut rendered = " ".repeat(2 * LEADING_SPACES);
                for item in split_keep(&COMMAND_SPLIT, &inner) {
                    if PARAM.is_match(item) {
                        rendered.push_str(
                            &PARAM.replace_all(item, |caps: &regex::Captures<'_>| colored(&caps[1], "parameter")),
                        );
                    } else {
                        rendered.push_str(&colored(item, "command"));
                    }
                }
                out.push_str(
                    &rendered
                        .replace("__ESCAPED_OPEN__", "{{")
                        .replace("__ESCAPED_CLOSE__", "}}"),
                );
            }
            out.push('\n');
        }
        out.push('\n');
        out
    }

    async fn update_cache(&self, language: Option<&str>, out: &mut Vec<String>) {
        let mut languages = self.languages.clone();
        if let Some(l) = language
            && !languages.iter().any(|x| x == l)
        {
            languages.push(l.to_string());
        }
        let base = self.download_cache.strip_suffix(".zip").unwrap_or(&self.download_cache);
        for language in languages {
            let location = format!("{base}-pages.{language}.zip");
            let result = async {
                let bytes = self.fetch(&location).await.map_err(|e| e.to_string())?;
                let mut archive = zip::ZipArchive::new(std::io::Cursor::new(bytes)).map_err(|e| e.to_string())?;
                let mut cached = 0;
                for i in 0..archive.len() {
                    let mut entry = archive.by_index(i).map_err(|e| e.to_string())?;
                    let name = entry.name().to_string();
                    let Some(caps) = ZIP_ENTRY.captures(&name) else {
                        continue;
                    };
                    let mut data = Vec::new();
                    std::io::Read::read_to_end(&mut entry, &mut data).map_err(|e| e.to_string())?;
                    self.store_to_cache(&data, &caps[2], &caps[1], &language);
                    cached += 1;
                }
                Ok::<_, String>(cached)
            }
            .await;
            match result {
                Ok(cached) => out.push(format!("Updated cache for language {language}: {cached} entries")),
                Err(_) => out.push(format!(
                    "Error: Unable to update cache for language {language} from {location}"
                )),
            }
        }
    }

    fn clear_cache(&self, language: Option<&str>, out: &mut Vec<String>) {
        let mut languages = self.languages.clone();
        if let Some(l) = language
            && !languages.iter().any(|x| x == l)
        {
            languages.push(l.to_string());
        }
        for language in languages {
            let dir = self.cache_dir.join(pages_dir(&language));
            if dir.is_dir() {
                match std::fs::remove_dir_all(&dir) {
                    Ok(()) => out.push(format!("Cleared cache for language {language}")),
                    Err(e) => out.push(format!(
                        "Error: Unable to delete cache directory {}: {e}",
                        dir.display()
                    )),
                }
            } else {
                out.push(format!("No cache directory found for language {language}"));
            }
        }
    }

    /// Run the tldr CLI with `args` and return its combined output.
    pub async fn run(&self, args: &[String]) -> String {
        let argv = std::iter::once("tldr".to_string()).chain(args.iter().cloned());
        let options = match TldrArgs::try_parse_from(argv) {
            Ok(options) => options,
            Err(e) => return e.render().to_string().trim().to_string(),
        };
        if options.version {
            return format!(
                "tldr (hashcli {}) (Client Specification {CLIENT_SPECIFICATION})",
                env!("CARGO_PKG_VERSION")
            );
        }

        let color = self.colors_enabled(options.color);
        let mut out: Vec<String> = Vec::new();

        let mut platform = options.platform.clone().map(|p| vec![p]);
        if platform.is_none() && !self.platform_env.is_empty() {
            match os_directory(&self.platform_env) {
                Some(dir) => platform = Some(vec![dir.to_string()]),
                None => out.push(format!(
                    "Warning: '{}' is not a supported TLDR_PLATFORM environment value.\nFalling back to auto-detection.",
                    self.platform_env
                )),
            }
        }

        let mut option_length = match self.options_env.as_str() {
            "short" => OptionLength::Short,
            "both" => OptionLength::Both,
            _ => OptionLength::Long,
        };
        if options.short_options {
            option_length = OptionLength::Short;
        }
        if options.long_options {
            option_length = OptionLength::Long;
        }
        if options.short_options && options.long_options {
            option_length = OptionLength::Both;
        }

        let language = options.language.as_deref();
        if options.update {
            self.update_cache(language, &mut out).await;
        }
        if options.clear_cache {
            self.clear_cache(language, &mut out);
            return out.join("\n").trim().to_string();
        }

        if options.list {
            out.push(self.get_commands(platform, language).join("\n"));
        } else if options.render {
            for command in &options.command {
                let path = Path::new(command);
                if let Ok(text) = std::fs::read_to_string(path) {
                    let lines: Vec<String> = text.lines().map(str::to_string).collect();
                    out.push(self.render(&lines, option_length, options.markdown, color));
                }
            }
        } else if let Some(search) = &options.search {
            let term = search.to_lowercase();
            let commands = self.get_commands(platform, language);
            if commands.is_empty() {
                out.push("Update cache, no commands to check from.".to_string());
            } else {
                let similar: Vec<&String> = commands.iter().filter(|c| c.to_lowercase().contains(&term)).collect();
                if similar.is_empty() {
                    out.push("No commands matched your search term.".to_string());
                } else {
                    out.push("Similar commands found:".to_string());
                    out.push(similar.iter().map(|s| s.as_str()).collect::<Vec<_>>().join("\n"));
                }
            }
        } else if !options.command.is_empty() {
            let command = options.command.join("-").to_lowercase();
            let source = options.source.clone().unwrap_or_else(|| self.pages_source.clone());
            let explicit_platform = platform.is_some();
            let languages = options.language.clone().map(|l| vec![l]);
            match self
                .get_page_for_every_platform(&command, &source, platform, languages)
                .await
            {
                Ok(results) if results.is_empty() => out.push(format!(
                    "`{command}` documentation is not available.\n\
                     If you want to contribute it, feel free to send a pull request to: https://github.com/tldr-pages/tldr"
                )),
                Ok(results) => {
                    out.push(self.render(&results[0].0, option_length, options.markdown, color));
                    let found_platform = &results[0].1;
                    if found_platform != &self.platform && found_platform != "common" && !explicit_platform {
                        let suffix = format!(
                            ": showing page from platform '{found_platform}', because '{command}' does not exist in '{}' and 'common'.",
                            self.platform
                        );
                        let label = if color && !options.markdown {
                            "\x1b[33mwarning\x1b[0m"
                        } else {
                            "warning"
                        };
                        out.push(format!("{label}{suffix}"));
                    }
                    let others: Vec<&str> = results[1..].iter().map(|(_, p)| p.as_str()).collect();
                    if others.len() > 1 {
                        out.push(format!(
                            "Found {} pages with the same name under the platforms: {}.",
                            others.len(),
                            others.join(", ")
                        ));
                    } else if let Some(other) = others.first() {
                        out.push(format!("Found 1 page with the same name under the platform: {other}."));
                    }
                }
                Err(e) => out.push(format!("Error fetching from tldr: {e}")),
            }
        } else if !options.update {
            let mut cmd = <TldrArgs as clap::CommandFactory>::command();
            out.push(cmd.render_help().to_string());
        }

        out.join("\n").trim().to_string()
    }
}

/// Plain tldr output for a command lookup (used by the LLM tool).
pub async fn lookup_command_examples(
    command: &str,
    platform: Option<&str>,
    language: Option<&str>,
    search: bool,
) -> String {
    let normalized = command.trim();
    if normalized.is_empty() {
        return "No command provided for tldr lookup.".to_string();
    }
    let mut args = vec!["--markdown".to_string()];
    if let Some(p) = platform {
        let dir = os_directory(&p.to_lowercase())
            .map(str::to_string)
            .unwrap_or_else(|| p.to_string());
        args.extend(["--platform".to_string(), dir]);
    }
    if let Some(l) = language {
        args.extend(["--language".to_string(), l.to_string()]);
    }
    if search {
        args.extend(["--search".to_string(), normalized.to_string()]);
    } else {
        match shlex::split(normalized) {
            Ok(words) => args.extend(words),
            Err(e) => return format!("Error: {e}"),
        }
    }
    Tldr::from_env().run(&args).await
}

/// `lookup_tldr_command` tool.
pub struct TldrTool;

fn optional_trimmed(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

#[async_trait(?Send)]
impl Tool for TldrTool {
    fn name(&self) -> &'static str {
        "lookup_tldr_command"
    }

    async fn execute(&self, arguments: &Value, _config: &HashConfig) -> anyhow::Result<String> {
        let command = match arguments.get("command") {
            Some(Value::String(s)) => s.trim().to_string(),
            Some(Value::Null) | None => String::new(),
            Some(other) => other.to_string(),
        };
        let platform = optional_trimmed(arguments.get("platform"));
        let language = optional_trimmed(arguments.get("language"));
        let search = arguments.get("search").and_then(Value::as_bool).unwrap_or(false);
        Ok(lookup_command_examples(&command, platform.as_deref(), language.as_deref(), search).await)
    }

    fn requires_confirmation(&self) -> bool {
        false
    }
}

/// Built-in `/tldr` slash command.
pub struct TldrCommand;

#[async_trait(?Send)]
impl Command for TldrCommand {
    async fn execute(&self, args: &[String], _config: &HashConfig) -> anyhow::Result<String> {
        Ok(Tldr::from_env().run(args).await)
    }

    fn help(&self) -> String {
        "Show TLDR pages for a command:
  /tldr <command>          - Show quick examples for a command
  /tldr --list             - List available commands
  /tldr --search <term>    - Search commands
  /tldr --update           - Download all pages into the local cache

Examples:
  /tldr tar
  /tldr --search docker"
            .to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TAR_PAGE: &str = "# tar\n\n> Archiving utility.\n> More information: <https://www.gnu.org/software/tar>.\n\n- [c]reate an archive from `files`:\n\n`tar {{[-c|--create]}} {{[-f|--file]}} {{path/to/target.tar}} {{path/to/file1}}`\n";

    fn tldr_with_source(dir: &Path) -> Tldr {
        let pages = dir.join("pages");
        std::fs::create_dir_all(pages.join("common")).unwrap();
        std::fs::create_dir_all(pages.join("osx")).unwrap();
        std::fs::write(pages.join("common").join("tar.md"), TAR_PAGE).unwrap();
        std::fs::write(pages.join("osx").join("pbcopy.md"), "# pbcopy\n\n> Copy.\n").unwrap();
        Tldr {
            pages_source: format!("file://{}", pages.display()),
            download_cache: DEFAULT_DOWNLOAD_CACHE.into(),
            use_cache: true,
            max_cache_age_hours: 168.0,
            cache_dir: dir.join("cache"),
            system_cache_dir: dir.join("system"),
            languages: vec!["en".into()],
            platform: "linux".into(),
            platform_env: String::new(),
            options_env: String::new(),
            force_color: false,
            no_color: false,
            color_overrides: HashMap::new(),
            cert: None,
            allow_insecure: false,
        }
    }

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn language_codes_and_lists() {
        assert_eq!(get_language_code("en_US.UTF-8"), "en");
        assert_eq!(get_language_code("pt_BR.UTF-8"), "pt_BR");
        assert_eq!(get_language_code("pt"), "pt_PT");
        assert_eq!(language_list("", "", "C"), vec!["en"]);
        assert_eq!(language_list("", "", "de_DE.UTF-8"), vec!["de", "en"]);
        assert_eq!(language_list("fr", "es:de", "de_DE"), vec!["fr", "es", "de", "en"]);
        assert_eq!(language_list("fr", "es", "C"), vec!["fr", "en"]);
    }

    #[test]
    fn page_urls_are_quoted() {
        let dir = tempfile::tempdir().unwrap();
        let tldr = tldr_with_source(dir.path());
        assert_eq!(
            tldr.page_url("git commit", "linux", "https://x/pages", "en"),
            "https://x/pages/linux/git%20commit.md"
        );
        assert_eq!(
            tldr.page_url("tar", "osx", "https://x/pages", "de"),
            "https://x/pages.de/osx/tar.md"
        );
    }

    #[test]
    fn split_keep_matches_python_split() {
        assert_eq!(split_keep(&EXAMPLE_SPLIT, "- a `b` c"), vec!["- a ", "`b`", " c"]);
        assert_eq!(split_keep(&COMMAND_SPLIT, "tar {{x}} y"), vec!["tar ", "{{x}}", " y"]);
    }

    #[test]
    fn renders_plain_and_formatted_pages() {
        let dir = tempfile::tempdir().unwrap();
        let tldr = tldr_with_source(dir.path());
        let lines: Vec<String> = TAR_PAGE.lines().map(str::to_string).collect();
        let plain = tldr.render(&lines, OptionLength::Long, true, false);
        assert!(plain.starts_with("# tar\n\n> Archiving utility."));

        let long = tldr.render(&lines, OptionLength::Long, false, false);
        assert!(long.contains("  tar\n"));
        assert!(long.contains(" Archiving utility."));
        assert!(long.contains("\n  - [c]reate an archive from files:"));
        assert!(long.contains("    tar --create --file path/to/target.tar path/to/file1"));

        let short = tldr.render(&lines, OptionLength::Short, false, false);
        assert!(short.contains("    tar -c -f path/to/target.tar"));

        let colored = tldr.render(&lines, OptionLength::Long, false, true);
        assert!(colored.contains("\x1b[31mtar --create --file \x1b[0m"));
        assert!(colored.contains("\x1b[3m\x1b[33mfiles\x1b[0m\x1b[23m"));
    }

    #[tokio::test]
    async fn looks_up_pages_from_file_source_and_caches() {
        let dir = tempfile::tempdir().unwrap();
        let tldr = tldr_with_source(dir.path());
        let output = tldr.run(&args(&["--markdown", "tar"])).await;
        assert!(output.starts_with("# tar"), "{output}");
        assert!(dir.path().join("cache/pages/common/tar.md").exists());
        assert_eq!(tldr.get_commands(None, None), vec!["tar"]);
        let search = tldr.run(&args(&["--search", "ta"])).await;
        assert_eq!(search, "Similar commands found:\ntar");
        let none = tldr.run(&args(&["--search", "zzz"])).await;
        assert_eq!(none, "No commands matched your search term.");
    }

    #[tokio::test]
    async fn reports_other_platform_and_missing_pages() {
        let dir = tempfile::tempdir().unwrap();
        let tldr = tldr_with_source(dir.path());
        let output = tldr.run(&args(&["-m", "pbcopy"])).await;
        assert!(output.contains("# pbcopy"));
        assert!(output.contains("warning: showing page from platform 'osx'"), "{output}");

        let missing = tldr.run(&args(&["-m", "does-not-exist"])).await;
        assert!(
            missing.starts_with("`does-not-exist` documentation is not available."),
            "{missing}"
        );
    }

    #[tokio::test]
    async fn rejects_invalid_platform_and_shows_help() {
        let dir = tempfile::tempdir().unwrap();
        let tldr = tldr_with_source(dir.path());
        let output = tldr.run(&args(&["--platform", "beos", "tar"])).await;
        assert!(output.contains("invalid value 'beos'"), "{output}");
        let help = tldr.run(&[]).await;
        assert!(help.contains("Usage: tldr command [options]"), "{help}");
    }

    #[tokio::test]
    async fn clear_cache_removes_pages() {
        let dir = tempfile::tempdir().unwrap();
        let tldr = tldr_with_source(dir.path());
        tldr.run(&args(&["-m", "tar"])).await;
        let output = tldr.run(&args(&["--clear-cache"])).await;
        assert_eq!(output, "Cleared cache for language en");
        assert!(!dir.path().join("cache/pages").exists());
    }

    #[tokio::test]
    async fn tool_handles_empty_command() {
        let out = TldrTool
            .execute(&serde_json::json!({"command": "  "}), &HashConfig::default())
            .await
            .unwrap();
        assert_eq!(out, "No command provided for tldr lookup.");
        assert!(!TldrTool.requires_confirmation());
    }
}
