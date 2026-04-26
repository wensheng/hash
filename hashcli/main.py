"""Main entry point for Hash CLI with dual-mode functionality."""

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path

    # Add the project root to the Python path
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    __package__ = "hashcli"


import asyncio
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from typing import Dict, Optional
from pathlib import Path
from typing import List, Tuple

import typer
from rich import box
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Confirm, Prompt

from hashcli.command_proxy import (
    CommandProxy,
    get_user_plugin_directory,
    load_command_class_from_file,
)
from hashcli.config import (
    ConfigurationError,
    LLMProvider,
    default_model,
    get_model_options,
    load_configuration,
    update_config_values,
    validate_api_setup,
)
from hashcli.llm_handler import LLMHandler
from hashcli.ui import console

# Initialize Typer app with rich formatting
app = typer.Typer(
    name="hashcli",
    help="Hash - Intelligent CLI assistant with dual-mode functionality",
    add_completion=False,  # We'll handle completion in shell integration
    rich_markup_mode="rich",
    no_args_is_help=False,  # Allow no args to show usage info
    context_settings={"help_option_names": ["-h", "--help"]},
)


@dataclass(frozen=True)
class QueryExecutionPolicy:
    force_tool_confirmation: Optional[bool]
    command_confirmation: bool
    force_command_oriented: bool = False


def _normalize_shell_input(input_text: str) -> str:
    """Normalize shell integration input that uses # markers."""
    if not input_text:
        return input_text

    leading_hash_match = re.match(r"^\s*#(.*)$", input_text, re.DOTALL)
    if leading_hash_match:
        return leading_hash_match.group(1).lstrip()

    hash_index = input_text.find("#")
    if hash_index <= 0:
        return input_text

    command_hint = input_text[:hash_index].strip()
    user_intent = input_text[hash_index + 1 :].strip()
    if not command_hint or not user_intent:
        return input_text

    return (
        f"Task: {user_intent}. "
        f"Use `{command_hint}` as command hint. "
        "Execute a single concrete shell command now and return the command result."
    )


def _is_how_to_query(input_text: str) -> bool:
    if not input_text:
        return False
    normalized = input_text.strip().lower()
    return bool(re.match(r"^(how to|how do i|how can i|what command|which command)\b", normalized))


def _is_embedded_hash_hint(raw_input: str) -> bool:
    if not raw_input or re.match(r"^\s*#", raw_input):
        return False
    hash_index = raw_input.find("#")
    if hash_index <= 0:
        return False
    command_hint = raw_input[:hash_index].strip()
    user_intent = raw_input[hash_index + 1 :].strip()
    return bool(command_hint and user_intent)


def _build_query_execution_policy(
    raw_input: str,
    normalized_input: str,
    command_confirmation: bool,
) -> QueryExecutionPolicy:
    if _is_embedded_hash_hint(raw_input):
        return QueryExecutionPolicy(
            force_tool_confirmation=False,
            command_confirmation=False,
            force_command_oriented=True,
        )

    if _is_how_to_query(normalized_input):
        return QueryExecutionPolicy(
            force_tool_confirmation=True,
            command_confirmation=True,
            force_command_oriented=True,
        )

    return QueryExecutionPolicy(
        force_tool_confirmation=None,
        command_confirmation=command_confirmation,
        force_command_oriented=False,
    )


def _is_interactive_session() -> bool:
    """Return True when invoked from an interactive terminal session."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _get_active_tty() -> str:
    """Get best-effort TTY name across stdio streams."""
    for fd in (0, 1, 2):
        try:
            if os.isatty(fd):
                return os.ttyname(fd)
        except Exception:
            continue
    return "no-tty"


def _build_shell_scope_fingerprint() -> str:
    """Build a stable fingerprint representing the current shell session."""
    env_markers = [
        os.environ.get("TMUX", ""),
        os.environ.get("STY", ""),
        os.environ.get("TERM_SESSION_ID", ""),
        os.environ.get("WT_SESSION", ""),
        os.environ.get("TERM_PROGRAM", ""),
    ]
    parts = [str(os.getppid()), _get_active_tty(), *env_markers]
    return "|".join(parts)


def _resolve_conversation_session_id(
    new_session: bool = False,
    requested_session_id: Optional[str] = None,
    config=None,
) -> Optional[str]:
    """Resolve conversation session id for this invocation."""
    if new_session and requested_session_id:
        raise ConfigurationError("--session cannot be combined with --new-session.")

    if new_session:
        return str(uuid.uuid4())

    if requested_session_id:
        from hashcli.history import ConversationHistory

        if config is not None and not config.history_enabled:
            raise ConfigurationError("--session requires history to be enabled.")

        history = ConversationHistory(config.history_dir if config is not None else None)
        resolved = history.resolve_session_id(requested_session_id)
        if resolved:
            return resolved

        matches = history.find_session_ids(requested_session_id)
        if len(matches) > 1:
            match_list = "\n".join(f"  {match}" for match in matches[:10])
            raise ConfigurationError(f"Ambiguous session ID prefix {requested_session_id!r}. Matches:\n{match_list}")
        raise ConfigurationError(f"No session found for {requested_session_id!r}.")

    existing_session_id = os.environ.get("HASHCLI_SESSION_ID", "").strip()
    if existing_session_id:
        return existing_session_id

    if not _is_interactive_session():
        return None

    fingerprint = _build_shell_scope_fingerprint()
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
    return f"shell-{digest}"


def _resolve_provider_option(provider: str) -> LLMProvider:
    """Accept full provider names plus single-letter aliases."""
    normalized = provider.strip().lower()
    aliases = {
        "a": LLMProvider.ANTHROPIC,
        "anthropic": LLMProvider.ANTHROPIC,
        "g": LLMProvider.GOOGLE,
        "google": LLMProvider.GOOGLE,
        "o": LLMProvider.OPENAI,
        "openai": LLMProvider.OPENAI,
    }

    if normalized in aliases:
        return aliases[normalized]

    valid = "a/anthropic, g/google, o/openai"
    raise ValueError(f"Invalid provider: {provider}. Expected one of {valid}.")


def _resolve_add_cmd_source(add_cmd_value: str) -> Path:
    """Resolve --add-cmd input to a Python plugin file."""
    source_path = Path(add_cmd_value).expanduser()
    if not source_path.exists():
        raise ValueError(f"Plugin source does not exist: {source_path}")

    if source_path.is_file():
        if source_path.suffix != ".py":
            raise ValueError(f"Plugin source must be a .py file: {source_path}")
        return source_path.resolve()

    if source_path.is_dir():
        python_files = sorted(
            path
            for path in source_path.rglob("*.py")
            if path.is_file() and path.name != "__init__.py" and not path.name.startswith("_")
        )
        if not python_files:
            raise ValueError(f"No Python plugin file found in directory: {source_path}")
        if len(python_files) == 1:
            return python_files[0].resolve()

        name_matched = [path for path in python_files if path.stem.lower() == source_path.name.lower()]
        if len(name_matched) == 1:
            return name_matched[0].resolve()

        candidates = ", ".join(path.name for path in python_files)
        raise ValueError(
            f"Directory contains multiple Python files. Provide a specific plugin file path. Candidates: {candidates}"
        )

    raise ValueError(f"Invalid plugin source path: {source_path}")


def _validate_plugin_in_subprocess(source_file: Path) -> None:
    """Validate plugin import in a child Python process before installation."""
    project_root = Path(__file__).resolve().parent.parent
    code = """
import sys
from pathlib import Path
from hashcli.command_proxy import load_command_class_from_file

plugin_path = Path(sys.argv[1])
command_class = load_command_class_from_file(plugin_path)
command = command_class()
help_text = command.get_help()
print(command_class.__name__)
print(str(help_text).splitlines()[0] if str(help_text).splitlines() else "")
"""
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(project_root) if not existing_pythonpath else f"{project_root}{os.pathsep}{existing_pythonpath}"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(source_file)],
        text=True,
        capture_output=True,
        cwd=str(project_root),
        env=env,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise ValueError(f"Plugin validation failed before install: {details}")


def _preview_plugin(add_cmd_value: str) -> Tuple[str, Path, str, str, Path]:
    """Return command metadata for a plugin source without installing it."""
    source_file = _resolve_add_cmd_source(add_cmd_value)
    _validate_plugin_in_subprocess(source_file)
    command_class = load_command_class_from_file(source_file)
    command = command_class()
    command_name = source_file.stem.lower()
    destination_file = get_user_plugin_directory() / source_file.name
    help_text = command.get_help()
    first_help_line = str(help_text).splitlines()[0] if str(help_text).splitlines() else ""
    return command_name, source_file, command_class.__name__, first_help_line, destination_file


def _install_plugin_from_path(add_cmd_value: str, yes: bool = False) -> Tuple[str, Path, str]:
    """Validate and install a third-party slash-command plugin."""
    command_name, source_file, class_name, first_help_line, destination_file = _preview_plugin(add_cmd_value)

    plugin_dir = get_user_plugin_directory()
    plugin_dir.mkdir(parents=True, exist_ok=True)

    console.print("[bold]Plugin preview[/bold]")
    console.print(f"Command: [cyan]/{command_name}[/cyan]")
    console.print(f"Class: [cyan]{class_name}[/cyan]")
    console.print(f"Install path: [dim]{destination_file}[/dim]")
    if first_help_line:
        console.print(f"Help: {first_help_line}")

    if not yes:
        if not _is_interactive_session():
            raise ValueError("Refusing to install plugin non-interactively without --yes.")
        if not _confirm_or_default("Install this plugin?", default=False):
            raise ValueError("Plugin install cancelled.")

    shutil.copy2(source_file, destination_file)

    # Re-validate from installation path to ensure runtime loading works.
    installed_class = load_command_class_from_file(destination_file)

    return command_name, destination_file, installed_class.__name__


def _list_installed_plugins() -> List[Dict[str, str]]:
    plugin_dir = get_user_plugin_directory()
    plugins = []
    if not plugin_dir.exists():
        return plugins
    for plugin_file in sorted(plugin_dir.glob("*.py")):
        if plugin_file.name.startswith("_"):
            continue
        command_name = plugin_file.stem.lower()
        try:
            command_class = load_command_class_from_file(plugin_file)
            command = command_class()
            help_lines = str(command.get_help()).splitlines()
            first_help_line = help_lines[0] if help_lines else ""
            class_name = command_class.__name__
        except Exception as exc:
            class_name = "<load failed>"
            first_help_line = str(exc)
        plugins.append(
            {
                "command": command_name,
                "class": class_name,
                "path": str(plugin_file),
                "help": first_help_line,
            }
        )
    return plugins


def _print_installed_plugins() -> None:
    plugins = _list_installed_plugins()
    if not plugins:
        console.print("No plugins installed.")
        return
    for plugin in plugins:
        console.print(
            f"/{plugin['command']}\t{plugin['class']}\t{plugin['path']}\t{plugin['help']}",
            markup=False,
        )


def _remove_plugin_command(command_name: str, yes: bool = False) -> Path:
    normalized = command_name.strip().lstrip("/").lower()
    if not normalized:
        raise ValueError("Plugin command name is required.")
    plugin_file = get_user_plugin_directory() / f"{normalized}.py"
    if not plugin_file.exists():
        raise ValueError(f"No installed plugin found for /{normalized}.")
    if not yes:
        if not _is_interactive_session():
            raise ValueError("Refusing to remove plugin non-interactively without --yes.")
        if not _confirm_or_default(f"Remove /{normalized} plugin?", default=False):
            raise ValueError("Plugin removal cancelled.")
    plugin_file.unlink()
    return plugin_file


def _print_completion_commands() -> None:
    try:
        config = load_configuration()
    except Exception:
        config = None
    proxy = CommandProxy(config or type("CompletionConfig", (), {"show_debug": False})())
    for command_name in proxy.get_available_commands():
        help_text = proxy.get_command_help(command_name) or ""
        first_help_line = help_text.splitlines()[0] if help_text.splitlines() else ""
        sys.stdout.write(f"{command_name}\t{first_help_line}\n")


def _strip_execute_prompt_lines(response_text: str) -> str:
    """Remove model-emitted execution prompt lines from displayed output."""
    if not response_text:
        return response_text

    lines = response_text.splitlines()
    filtered = [line for line in lines if not _is_execute_prompt_line(line)]
    return "\n".join(filtered).strip()


def _extract_command_description(response_text: str, command: str) -> str:
    """Extract a short description near a suggested command."""
    if not response_text:
        return ""

    lines = [line.strip() for line in response_text.splitlines()]
    command_indexes = [index for index, line in enumerate(lines) if command in line]
    search_indexes = []
    for index in command_indexes:
        search_indexes.extend(range(max(0, index - 3), index + 1))
    if not search_indexes:
        search_indexes = list(range(min(3, len(lines))))

    for index in search_indexes:
        line = lines[index]
        if not line or _is_execute_prompt_line(line) or line.startswith("```"):
            continue
        line = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)", "", line).strip()
        line = re.sub(r"`[^`]+`", "", line).strip(" :-")
        if line and len(line.split()) >= 3:
            return line[:160]
    return ""


def _is_execute_prompt_line(line: str) -> bool:
    if line.startswith("SUGGESTED_COMMAND:"):
        return True
    return bool(
        re.match(
            r"^\s*(?:do you want(?: me to)? execute|would you like(?: me)? to execute)\b",
            line,
            re.IGNORECASE,
        )
    )


def _is_command_oriented_query(user_query: Optional[str]) -> bool:
    """Only suggest command execution for shell-oriented requests."""
    if not user_query:
        return False

    query = user_query.strip().lower()
    if not query or query.startswith("/"):
        return False

    general_starters = (
        "why ",
        "what is ",
        "what are ",
        "who ",
        "where ",
        "when ",
        "explain ",
        "tell me about ",
    )
    if query.startswith(general_starters) and not re.search(
        r"\b(command|cli|shell|terminal|bash|zsh|powershell|cmd)\b", query
    ):
        return False

    if re.search(r"[|;&<>`$()]", query):
        return True

    action_patterns = (
        r"\bhow do i\b",
        r"\bhow to\b",
        r"\bcommand\b",
        r"\bcli\b",
        r"\bshell\b",
        r"\bterminal\b",
        r"\bbash\b",
        r"\bzsh\b",
        r"\bpowershell\b",
        r"\bcmd\b",
        r"\brun\b",
        r"\bexecute\b",
        r"\binstall\b",
        r"\buninstall\b",
        r"\bkill\b",
        r"\bstop\b",
        r"\brestart\b",
        r"\bdelete\b",
        r"\bremove\b",
        r"\blist\b",
        r"\bshow\b",
        r"\bcheck\b",
        r"\bfind\b",
        r"\bread\b",
    )
    if any(re.search(pattern, query) for pattern in action_patterns):
        return True

    command_tokens = (
        "ls",
        "pwd",
        "grep",
        "find",
        "cat",
        "sed",
        "awk",
        "git",
        "docker",
        "kubectl",
        "python",
        "pytest",
        "npm",
        "yarn",
        "pnpm",
    )
    return bool(re.search(rf"\b({'|'.join(command_tokens)})\b", query))


def _extract_suggested_command(
    response_text: str,
    user_query: Optional[str] = None,
    allow_shell_operators: bool = False,
) -> Optional[str]:
    if not response_text:
        return None

    def clean_command(command: str) -> str:
        cleaned = command.strip().rstrip("?.! ")
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'`":
            cleaned = cleaned[1:-1].strip()
        return cleaned

    def strip_bullet_prefix(line: str) -> str:
        stripped = line.strip()
        stripped = re.sub(r"^(?:[-*•]\s+)", "", stripped)
        stripped = re.sub(r"^\d+[.)]\s+", "", stripped)
        return stripped.strip()

    def is_probable_command(command: str) -> bool:
        if not command:
            return False
        if "\n" in command or "\r" in command:
            return False
        if len(command) > 256:
            return False
        if "?" in command:
            return False
        if command.strip().endswith(":"):
            return False

        # Reject commands with shell operators we do not allow to execute.
        forbidden_operators = ["&&", "||", "`", "$(", ">", "<"]
        if not allow_shell_operators:
            forbidden_operators.extend([";", "|"])
        for op in forbidden_operators:
            if op in command:
                return False

        lowered = command.lower()
        if re.search(r"\b(confirm|execute|execution|please)\b", lowered):
            return False

        try:
            parts = shlex.split(command)
        except ValueError:
            return False

        if not parts:
            return False

        first_raw = parts[0]
        first = first_raw.lower()
        if first in {
            "this",
            "that",
            "it",
            "you",
            "your",
            "confirm",
            "execute",
            "please",
        }:
            return False

        command_allowlist = {
            "ls",
            "pwd",
            "whoami",
            "date",
            "tail",
            "head",
            "cat",
            "kill",
            "killall",
            "pkill",
            "lsof",
            "fuser",
            "grep",
            "rg",
            "find",
            "xargs",
            "sort",
            "uniq",
            "wc",
            "du",
            "df",
            "ps",
            "top",
            "git",
            "pip",
            "python",
            "pytest",
            "npm",
            "yarn",
            "pnpm",
            "node",
            "cargo",
            "go",
            "make",
            "docker",
            "docker-compose",
            "kubectl",
            "curl",
            "wget",
            "sed",
            "awk",
            "chmod",
            "chown",
            "cp",
            "mv",
            "rm",
            "mkdir",
            "rmdir",
            "touch",
            "tree",
            "uname",
            "whereis",
            "which",
            "stat",
            "ln",
            "tar",
            "zip",
            "unzip",
            "ssh",
            "scp",
            "less",
            "more",
            "echo",
            "printf",
            "env",
            "seq",
            "cut",
            "tr",
            "diff",
            "cmp",
        }

        if (
            "/" not in first_raw
            and not first_raw.startswith("./")
            and not first_raw.startswith("~/")
            and first not in command_allowlist
        ):
            return False

        return True

    def extract_prefixed_command(line: str) -> Optional[str]:
        prefixes = ("command", "run", "use", "try")
        lowered = line.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                remainder = line[len(prefix) :]
                # Only accept explicit command prefixes like:
                # "Run: ls -la", "Use - ls", or "Try `git status`".
                stripped_remainder = remainder.lstrip()
                if not stripped_remainder.startswith((":", "-", "`", "$")):
                    continue
                remainder = stripped_remainder
                if remainder.startswith((":", "-")):
                    remainder = remainder[1:].lstrip()
                candidate = clean_command(remainder)
                if is_probable_command(candidate):
                    return candidate
        return None

    def extract_query_tokens(text: Optional[str]) -> List[str]:
        if not text:
            return []
        tokens = re.findall(r"[A-Za-z0-9_./-]+", text)
        filtered = []
        for token in tokens:
            if "/" in token or "." in token or "__" in token:
                filtered.append(token.lower())
        return filtered

    query_tokens = extract_query_tokens(user_query)

    def normalize_command(command: str) -> str:
        try:
            parts = shlex.split(command)
        except ValueError:
            return command

        if not parts:
            return command

        cwd = Path.cwd()
        normalized: List[str] = []
        for part in parts:
            replaced = False
            if part.startswith("/path/to/") and query_tokens:
                for token in query_tokens:
                    if token.endswith(".md") or token.endswith(".txt"):
                        normalized.append(token)
                        replaced = True
                        break
            if replaced:
                continue

            if os.path.isabs(part):
                try:
                    rel = Path(part).resolve().relative_to(cwd)
                    normalized.append(str(rel))
                except Exception:
                    normalized.append(part)
            else:
                normalized.append(part)

        def quote_part(part: str) -> str:
            if re.search(r"\s", part):
                return shlex.quote(part)
            return part

        return " ".join(quote_part(part) for part in normalized)

    def add_candidate(candidate: str, source: str, position: int = 0, normalize: bool = True) -> Optional[dict]:
        cleaned = clean_command(candidate)
        if not is_probable_command(cleaned):
            return None
        if normalize:
            cleaned = normalize_command(cleaned)
        base_scores = {
            "explicit": 100,
            "fence": 90,
            "dollar": 85,
            "prefixed": 80,
            "inline": 70,
            "bare": 60,
        }
        score = base_scores.get(source, 50)

        try:
            parts = shlex.split(cleaned)
        except ValueError:
            parts = cleaned.split()

        if len(parts) >= 2:
            score += 10
        if len(parts) == 1:
            score -= 20
        if query_tokens and any(token in cleaned.lower() for token in query_tokens):
            score += 20
        if re.search(r"\s-", cleaned):
            score += 5
        # Prefer shorter commands when otherwise similar.
        score -= min(len(cleaned), 200) / 200
        return {
            "score": score,
            "position": position,
            "command": cleaned,
            "parts": parts,
        }

    candidates = []

    explicit_patterns = (
        r"do you want(?: me to)? execute\s+`([^`]+)`",
        r"would you like(?: me)? to execute\s+`([^`]+)`",
        r"SUGGESTED_COMMAND:\s*`?([^`\n]+)`?",
    )
    for pattern in explicit_patterns:
        for match in re.finditer(pattern, response_text, re.IGNORECASE):
            item = add_candidate(match.group(1), "explicit", match.start(), normalize=False)
            if item:
                candidates.append(item)

    fence_matches = re.findall(r"```[a-zA-Z0-9_-]*\s*([^`]+?)\s*```", response_text, re.DOTALL)
    for idx, fence in enumerate(fence_matches):
        for line in fence.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("$"):
                stripped = stripped.lstrip("$").strip()
            item = add_candidate(stripped, "fence", idx)
            if item:
                candidates.append(item)

    lines = response_text.splitlines()
    for idx, line in enumerate(lines):
        stripped = strip_bullet_prefix(line)
        if not stripped:
            continue
        prefixed = extract_prefixed_command(stripped)
        if prefixed:
            item = add_candidate(prefixed, "prefixed", idx)
            if item:
                candidates.append(item)
            continue
        if stripped.startswith("$"):
            item = add_candidate(stripped.lstrip("$").strip(), "dollar", idx)
            if item:
                candidates.append(item)
            continue

    if not candidates:
        return None

    if query_tokens:
        matched = [c for c in candidates if all(token in c["command"].lower() for token in query_tokens)]
        if matched:
            matched.sort(key=lambda c: (c["score"], c["position"]), reverse=True)
            return matched[0]["command"]

    candidates.sort(key=lambda c: (c["score"], c["position"]), reverse=True)
    return candidates[0]["command"]


async def _maybe_execute_suggested_command(
    response_text: str,
    config,
    quiet: bool = False,
    user_query: Optional[str] = None,
    command_confirmation: bool = True,
) -> None:
    suggested_command = _extract_suggested_command(
        response_text,
        user_query=user_query,
        allow_shell_operators=config.allow_shell_operators,
    )
    if not suggested_command:
        return

    from hashcli.tools.shell import ShellTool

    must_confirm = command_confirmation or ShellTool.is_potentially_destructive_command(suggested_command)

    if must_confirm:
        suggested_command = await _confirm_suggested_command(
            suggested_command,
            response_text,
            config,
        )
        if not suggested_command:
            return

    tool = ShellTool()
    result = await tool.execute(
        {
            "command": suggested_command,
            "description": "User-confirmed command execution",
            "passthrough_output": True,
        },
        config,
    )
    if result:
        display_result(result, config, quiet)


async def _confirm_suggested_command(command: str, response_text: str, config) -> Optional[str]:
    """Prompt for suggested command execution with explain/edit choices."""
    if not _is_interactive_session():
        console.print("[yellow]Suggested command requires confirmation; skipping in non-interactive mode.[/yellow]")
        return None

    current_command = command
    while True:
        description = _extract_command_description(response_text, current_command)
        console.print("\n[bold yellow]Suggested command[/bold yellow]")
        if description:
            console.print(f"Description: {description}", markup=False)
        console.print("Command: ", end="")
        console.print(current_command, style="cyan", markup=False)
        choice = Prompt.ask(
            "Choose [y] execute, [n] cancel, [x] explain, [e] edit",
            choices=["y", "n", "x", "e"],
            default="n",
            console=console,
        )
        if choice == "y":
            return current_command
        if choice == "n":
            return None
        if choice == "x":
            explanation = await _explain_command_with_llm(current_command, config)
            display_result(explanation, config)
            continue
        if choice == "e":
            edited = _edit_command_in_editor(current_command)
            if edited:
                current_command = edited


async def _explain_command_with_llm(command: str, config) -> str:
    explanation_config = copy.deepcopy(config)
    explanation_config.allow_command_execution = False
    explanation_config.tool_confirmation = True
    explanation_config.command_confirmation = True
    explanation_config.streaming = False
    handler = LLMHandler(explanation_config)
    return await handler.chat(
        "Explain what this shell command does, piece by piece, without executing it:\n" f"{command}",
        force_tool_confirmation=True,
    )


def _edit_command_in_editor(command: str) -> Optional[str]:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        console.print("[yellow]Set VISUAL or EDITOR to edit suggested commands.[/yellow]")
        return command

    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, prefix="hashcli-command-", suffix=".sh"
        ) as temp_file:
            temp_file.write(command + "\n")
            temp_path = Path(temp_file.name)
        subprocess.run(shlex.split(editor) + [str(temp_path)], check=False)
        edited = temp_path.read_text(encoding="utf-8").strip()
        return edited or command
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def show_welcome():
    """Display welcome message with usage instructions."""
    welcome_text = """
# Hash CLI - Command Assistant

## Usage Modes

**LLM Chat Mode** (Natural language queries):
```bash
hashcli how do I list large files?
hashcli what command shows disk usage by directory?
hashcli explain xargs
```

**Command Proxy Mode** (Direct commands with `/` prefix):
```bash
hashcli /help
hashcli /history list
hashcli --add-cmd plugins/hello.py
```

## Quick Start
1. Set your API key: `export OPENAI_API_KEY="your-key"`
2. Try: `hashcli how do I check disk usage?`
3. Or: `hashcli /help` for available commands

For more help: `hashcli --help`
    """

    console.print(
        Panel(
            Markdown(welcome_text),
            title="[bold blue]Hash CLI[/bold blue]",
            border_style="blue",
        )
    )


def version_callback(value: bool):
    """Show version and exit."""
    if value:
        try:
            from importlib.metadata import version, PackageNotFoundError

            package_version = version("hashcli")
        except (ImportError, PackageNotFoundError):
            from . import __version__ as package_version

        console.print(f"Hash CLI version {package_version}")
        raise typer.Exit()


def show_config_callback(value: bool):
    """Show current configuration and exit."""
    if value:
        try:
            config = load_configuration()

            console.print("\n[bold blue]Hash CLI Configuration[/bold blue]")
            console.print(f"Provider: [cyan]{config.llm_provider.value}[/cyan]")
            console.print(f"Model: [cyan]{config.get_current_model()}[/cyan]")
            console.print(f"API Key: [green]{'✓ Set' if config.get_current_api_key() else '✗ Not set'}[/green]")
            console.print(
                f"Command execution: [cyan]{'Enabled' if config.allow_command_execution else 'Disabled'}[/cyan]"
            )
            console.print(f"Command confirmation: [cyan]{'Yes' if config.command_confirmation else 'No'}[/cyan]")
            console.print(f"Tool confirmation: [cyan]{'Yes' if config.tool_confirmation else 'No'}[/cyan]")
            console.print(
                f"Shell operators (|, ;): [cyan]{'Allowed' if config.allow_shell_operators else 'Blocked'}[/cyan]"
            )
            console.print(f"History: [cyan]{'Enabled' if config.history_enabled else 'Disabled'}[/cyan]")
            if config.history_enabled:
                console.print(f"History location: [dim]{config.history_dir}[/dim]")
            console.print(f"Streaming: [cyan]{'Enabled' if config.streaming else 'Disabled'}[/cyan]")

        except Exception as e:
            console.print(f"[bold red]Error loading configuration:[/bold red] {e}")
            raise typer.Exit(1)
        raise typer.Exit()


def _provider_key_field(provider: LLMProvider) -> str:
    return {
        LLMProvider.OPENAI: "openai_api_key",
        LLMProvider.ANTHROPIC: "anthropic_api_key",
        LLMProvider.GOOGLE: "google_api_key",
    }[provider]


def _provider_model_field(provider: LLMProvider) -> str:
    return {
        LLMProvider.OPENAI: "openai_model",
        LLMProvider.ANTHROPIC: "anthropic_model",
        LLMProvider.GOOGLE: "google_model",
    }[provider]


def _provider_api_env_vars(provider: LLMProvider) -> List[str]:
    if provider == LLMProvider.OPENAI:
        return ["OPENAI_API_KEY", "HASHCLI_OPENAI_API_KEY"]
    if provider == LLMProvider.ANTHROPIC:
        return ["ANTHROPIC_API_KEY", "HASHCLI_ANTHROPIC_API_KEY"]
    return ["GOOGLE_API_KEY", "GEMINI_API_KEY", "HASHCLI_GOOGLE_API_KEY"]


def _get_existing_provider_key(provider: LLMProvider) -> Optional[str]:
    for env_var in _provider_api_env_vars(provider):
        value = os.environ.get(env_var, "").strip()
        if value:
            return value
    return None


def _confirm_or_default(question: str, default: bool = False) -> bool:
    try:
        return Confirm.ask(question, default=default, console=console)
    except (EOFError, OSError):
        return default


def _has_provider_api_key_env() -> bool:
    env_vars = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "HASHCLI_OPENAI_API_KEY",
        "HASHCLI_ANTHROPIC_API_KEY",
        "HASHCLI_GOOGLE_API_KEY",
    ]
    return any(os.environ.get(env_var, "").strip() for env_var in env_vars)


def _has_config_file(config_file: Optional[str] = None) -> bool:
    if config_file:
        return Path(config_file).expanduser().exists()
    return any(path.exists() for path in (Path.home() / ".hashcli" / "config.toml", Path("/etc/hashcli/config.toml")))


def _should_run_first_setup(config_file: Optional[str] = None) -> bool:
    return _is_interactive_session() and not _has_config_file(config_file) and not _has_provider_api_key_env()


async def _validate_setup_provider(config) -> Tuple[bool, str]:
    """Run a minimal provider request to validate setup."""
    try:
        handler = LLMHandler(config)
        response = await handler.provider.generate_response(
            messages=[
                {"role": "system", "content": "Reply with exactly: ok"},
                {"role": "user", "content": "ok"},
            ],
            tools=None,
        )
    except Exception as exc:
        return False, str(exc)

    content = (response.content or "").strip()
    lowered = content.lower()
    error_markers = (
        "authentication failed",
        "invalid",
        "api key",
        "rate limit",
        "quota",
        "model",
        "error",
    )
    if any(marker in lowered for marker in error_markers):
        return False, content
    return True, content or "ok"


def run_setup_wizard(exit_after: bool = True) -> None:
    """Run interactive setup wizard."""
    console.print("\n[bold blue]Hash CLI Setup Wizard[/bold blue]")
    console.print("This will help you configure Hash CLI for first use.\n")

    console.print("[bold]1. Choose your LLM provider:[/bold]")
    console.print("   1) OpenAI")
    console.print("   2) Anthropic")
    console.print("   3) Google Gemini")

    provider_choice = typer.prompt("Enter choice (1-3)", type=int)
    provider_by_choice = {
        1: LLMProvider.OPENAI,
        2: LLMProvider.ANTHROPIC,
        3: LLMProvider.GOOGLE,
    }
    provider = provider_by_choice.get(provider_choice)
    if provider is None:
        console.print("[red]Invalid choice[/red]")
        raise typer.Exit(1)
    console.print(f"\n[bold]{provider.value.capitalize()} selected[/bold]")

    console.print(f"\n[bold]2. Select {provider.value.capitalize()} model:[/bold]")
    options = get_model_options(provider)
    for i, model_name in enumerate(options, 1):
        console.print(f"   {i}) {model_name}")
    console.print(f"   {len(options) + 1}) Custom model name")

    model_or_key = typer.prompt(f"Select model (1-{len(options) + 1})", default="1", show_default=True)
    selected_model = default_model[provider]
    api_key: Optional[str] = None
    if model_or_key.isdigit():
        model_choice = int(model_or_key)
        if 1 <= model_choice <= len(options):
            selected_model = options[model_choice - 1]
        elif model_choice == len(options) + 1:
            selected_model = typer.prompt("Enter custom model name")
        else:
            console.print("[red]Invalid choice[/red]")
            raise typer.Exit(1)
    else:
        # Backward-compatible path for scripted setup that supplies provider then key.
        api_key = model_or_key

    if api_key is None:
        api_key = typer.prompt(
            "\nEnter your API key (leave blank to use an environment variable)",
            default="",
            show_default=False,
            hide_input=True,
        )
    api_key = api_key.strip()
    use_env_key = not api_key
    effective_key = api_key or _get_existing_provider_key(provider)

    streaming = _confirm_or_default("Enable streaming responses?", default=False)

    updates = {"llm_provider": provider.value, _provider_model_field(provider): selected_model, "streaming": streaming}
    if api_key:
        updates[_provider_key_field(provider)] = api_key

    if not update_config_values(updates):
        console.print("[red]Failed to save configuration[/red]")
        raise typer.Exit(1)

    if effective_key:
        validation_config = load_configuration()
        setattr(validation_config, _provider_key_field(provider), effective_key)
        validation_config.llm_provider = provider
        setattr(validation_config, _provider_model_field(provider), selected_model)
        validation_config.streaming = False
        ok, message = asyncio.run(_validate_setup_provider(validation_config))
        if not ok:
            console.print(f"[red]Provider validation failed:[/red] {message}")
            raise typer.Exit(1)
        console.print("[green]Provider validation succeeded.[/green]")
    elif use_env_key:
        env_vars = ", ".join(_provider_api_env_vars(provider))
        console.print(f"[yellow]Skipped provider validation because no API key was found in {env_vars}.[/yellow]")

    shell_setup_status = ensure_shell_integration()

    console.print("\n[bold green]Setup complete![/bold green]")
    if api_key:
        console.print("API key has been saved to ~/.hashcli/config.toml")
    else:
        console.print("API key will be read from your environment.")
    if shell_setup_status == "installed":
        console.print("Shell integration has been installed.")
    elif shell_setup_status == "skipped":
        console.print("Shell integration is already configured. Skipped.")
    elif shell_setup_status == "unsupported":
        console.print("Shell integration was skipped because the current shell is not supported.")
    elif shell_setup_status == "failed":
        console.print("Shell integration could not be installed automatically.")
    console.print("Try: [code]hashcli hello world[/code]")

    if exit_after:
        raise typer.Exit()


def config_callback(value: bool):
    """Run interactive setup wizard and exit."""
    if value:
        run_setup_wizard(exit_after=True)


def _get_shell_integration_metadata(shell_name: str) -> Optional[dict]:
    """Return shell-specific installation metadata."""
    home_dir = Path.home()
    if shell_name == "zsh":
        return {
            "rc_file": home_dir / ".zshrc",
            "source_line": "source ~/.config/zsh/hash/hash.zsh",
            "required_files": [
                home_dir / ".config" / "zsh" / "hash" / "hash.zsh",
                home_dir / ".config" / "zsh" / "hash" / "completions" / "_hash",
            ],
        }
    if shell_name == "bash":
        return {
            "rc_file": home_dir / ".bashrc",
            "source_line": "source ~/.config/bash/hash/hash.bash",
            "required_files": [
                home_dir / ".config" / "bash" / "hash" / "hash.bash",
                home_dir / ".config" / "bash" / "hash" / "hash_completion.bash",
            ],
        }
    if shell_name == "fish":
        return {
            "rc_file": None,
            "source_line": None,
            "required_files": [
                home_dir / ".config" / "fish" / "conf.d" / "hash_integration.fish",
                home_dir / ".config" / "fish" / "completions" / "completions.fish",
            ],
        }
    if shell_name in ("pwsh", "powershell", "powershell.exe", "pwsh.exe"):
        return {
            "rc_file": None,
            "source_line": None,
            "required_files": [
                home_dir / ".hashcli" / "powershell" / "hash.ps1",
                home_dir / ".hashcli" / "powershell" / "completions.ps1",
            ],
        }
    return None


def is_shell_integration_installed(shell_name: str) -> bool:
    """Check whether shell integration is already installed for the active shell."""
    metadata = _get_shell_integration_metadata(shell_name)
    if metadata is None:
        return False

    if not all(path.exists() for path in metadata["required_files"]):
        return False

    rc_file = metadata.get("rc_file")
    source_line = metadata.get("source_line")
    if rc_file is None or source_line is None:
        return True

    if not rc_file.exists():
        return False

    rc_contents = rc_file.read_text(encoding="utf-8", errors="ignore")
    return source_line in rc_contents


def ensure_shell_integration() -> str:
    """Install shell integration if needed and return installed/skipped/unsupported/failed."""
    shell_env = os.environ.get("SHELL", "")
    shell_name = Path(shell_env).name

    supported_shells = ("zsh", "bash", "fish", "pwsh", "powershell", "powershell.exe", "pwsh.exe")
    if shell_name not in supported_shells:
        console.print("[yellow]Shell integration setup supports zsh, bash, fish, and PowerShell.[/yellow]")
        console.print(f"Detected shell: [dim]{shell_name or 'unknown'}[/dim]")
        console.print("For other shells, use the scripts in the shell directory.")
        return "unsupported"

    if is_shell_integration_installed(shell_name):
        console.print(f"[dim]{shell_name} shell integration already configured, skipping.[/dim]")
        return "skipped"

    # Copy shell scripts to ~/.hashcli/shell/ if not already there
    user_shell_dir = Path.home() / ".hashcli" / "shell"
    install_shell_name = (
        "powershell" if shell_name in ("pwsh", "powershell", "powershell.exe", "pwsh.exe") else shell_name
    )
    install_script = (
        user_shell_dir / install_shell_name / ("install.ps1" if install_shell_name == "powershell" else "install.sh")
    )

    console.print("[dim]Setting up shell integration scripts...[/dim]")
    try:
        # Create directory structure
        user_shell_dir.mkdir(parents=True, exist_ok=True)

        # Copy shell scripts from package to user directory
        module_dir = Path(__file__).resolve().parent
        package_shell_dir = module_dir / "shell"

        if package_shell_dir.exists():
            # Copy the entire shell directory
            import shutil

            shutil.copytree(package_shell_dir, user_shell_dir, dirs_exist_ok=True)
            # Make scripts executable
            for script in user_shell_dir.rglob("*.sh"):
                script.chmod(0o755)
            # Make bash scripts executable
            for script in user_shell_dir.rglob("*.bash"):
                script.chmod(0o755)
            for script in user_shell_dir.rglob("*.ps1"):
                script.chmod(0o755)
            console.print(f"[dim]Shell scripts copied to {user_shell_dir}[/dim]")
        else:
            console.print("[bold red]Unable to locate shell scripts in package.[/bold red]")
            return "failed"
    except Exception as e:
        console.print(f"[bold red]Failed to copy shell scripts:[/bold red] {e}")
        return "failed"

    # Run install script from user directory
    console.print(f"[bold blue]Installing {shell_name} shell integration...[/bold blue]")
    try:
        if install_shell_name == "powershell":
            powershell_binary = shutil.which("pwsh") or shutil.which("powershell")
            if powershell_binary is None:
                console.print("[bold red]PowerShell executable not found.[/bold red]")
                return "failed"
            subprocess.run(
                [powershell_binary, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(install_script)],
                check=True,
            )
        else:
            subprocess.run(
                ["/bin/bash", str(install_script), "install"],
                check=True,
            )
    except subprocess.CalledProcessError as exc:
        console.print("[bold red]Shell integration setup failed.[/bold red]")
        return "failed"

    return "installed"


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    query: List[str] = typer.Argument(None, help="Query or command to execute. Use /command for proxy mode."),
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="Enable debug output and detailed error information",
    ),
    config_file: Optional[str] = typer.Option(None, "--config-file", "-f", help="Path to custom configuration file"),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Override default LLM model (e.g., gpt-5, claude-3-sonnet)",
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Override LLM provider (openai/anthropic/google or o/a/g)",
    ),
    new_session: bool = typer.Option(
        False,
        "--new-session",
        "-n",
        help="Start a new conversation session for this invocation.",
    ),
    session: Optional[str] = typer.Option(
        None,
        "--session",
        help="Resume a conversation session by full ID or unique prefix.",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimize output, show only results"),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version information and exit.",
    ),
    show_config: Optional[bool] = typer.Option(
        None,
        "--show-config",
        "-s",
        callback=show_config_callback,
        is_eager=True,
        help="Show current configuration and exit.",
    ),
    configure: Optional[bool] = typer.Option(
        None,
        "--config",
        "-c",
        callback=config_callback,
        is_eager=True,
        help="Configure Hash CLI and install shell integration if needed.",
    ),
    add_cmd: Optional[str] = typer.Option(
        None,
        "--add-cmd",
        "-a",
        help="Install a slash-command plugin from a .py file or folder.",
    ),
    list_plugins: bool = typer.Option(
        False,
        "--list-plugins",
        help="List installed slash-command plugins.",
    ),
    remove_cmd: Optional[str] = typer.Option(
        None,
        "--remove-cmd",
        help="Remove an installed slash-command plugin by command name.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm non-interactive plugin install/removal prompts.",
    ),
    completion_commands: bool = typer.Option(
        False,
        "--completion-commands",
        help="Print slash command names for shell completions.",
        hidden=True,
    ),
):
    """Main function for the Hash CLI."""
    if completion_commands:
        _print_completion_commands()
        raise typer.Exit(0)

    if list_plugins:
        _print_installed_plugins()
        raise typer.Exit(0)

    if remove_cmd:
        try:
            removed_path = _remove_plugin_command(remove_cmd, yes=yes)
            console.print(f"[green]Removed plugin:[/green] {removed_path}")
            raise typer.Exit(0)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

    if add_cmd:
        try:
            command_name, destination_file, class_name = _install_plugin_from_path(add_cmd, yes=yes)
            console.print(f"[green]Installed plugin:[/green] {destination_file}")
            console.print(f"[green]Validated command class:[/green] {class_name}")
            console.print(f"[green]Available slash command:[/green] /{command_name}")
            raise typer.Exit(0)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        except typer.Exit:
            raise
        except Exception as e:
            console.print(f"[red]Error adding plugin: {e}[/red]")
            raise typer.Exit(1)

    if not query:
        show_welcome()
        raise typer.Exit()

    raw_input = " ".join(query)
    input_text = _normalize_shell_input(raw_input)
    if not input_text.strip():
        show_welcome()
        raise typer.Exit()

    try:
        if new_session and session:
            raise ConfigurationError("--session cannot be combined with --new-session.")

        if input_text.startswith("config "):
            input_text = "/" + input_text

        if not input_text.startswith("/") and _should_run_first_setup(config_file):
            console.print("[yellow]No Hash CLI configuration or provider API key was found.[/yellow]")
            run_setup_wizard(exit_after=False)

        config = load_configuration(
            config_file=config_file,
            debug=debug,
            model_override=model,
        )

        # Update config with CLI options
        if provider:
            config.llm_provider = _resolve_provider_option(provider)

        query_policy = _build_query_execution_policy(raw_input, input_text, config.command_confirmation)

        # Decide execution mode
        if input_text.startswith("/"):
            execute_command_mode(input_text, config, quiet)
        else:
            # Validate API key setup
            validate_api_setup(config)
            session_id = _resolve_conversation_session_id(
                new_session=new_session,
                requested_session_id=session,
                config=config,
            )
            asyncio.run(
                execute_llm_mode(
                    input_text,
                    config,
                    quiet,
                    session_id=session_id,
                    force_tool_confirmation=query_policy.force_tool_confirmation,
                    command_confirmation=query_policy.command_confirmation,
                    force_command_oriented=query_policy.force_command_oriented,
                )
            )

    except ConfigurationError as e:
        handle_error(e, debug)
        console.print("\n[bold]Tip:[/bold] Run `hashcli --config` to get started.")
        raise typer.Exit(1)
    except Exception as e:
        handle_error(e, debug)
        raise typer.Exit(1)


def execute_command_mode(input_text: str, config, quiet: bool = False):
    """Execute command in proxy mode."""
    if not quiet:
        with console.status("[dim]Executing command...[/dim]"):
            handler = CommandProxy(config)
            result = handler.execute(input_text)
    else:
        handler = CommandProxy(config)
        result = handler.execute(input_text)

    if result:
        display_result(result, config, quiet)


async def execute_llm_mode(
    input_text: str,
    config,
    quiet: bool = False,
    session_id: Optional[str] = None,
    force_tool_confirmation: Optional[bool] = None,
    command_confirmation: bool = True,
    force_command_oriented: bool = False,
):
    """Execute query in LLM chat mode."""
    if session_id:
        handler = LLMHandler(config, session_id=session_id)
    else:
        handler = LLMHandler(config)

    if config.streaming:
        streamed_output = {"emitted": False, "buffer": [], "line_buffer": ""}

        def stream_handler(chunk: str) -> None:
            if not chunk:
                return
            streamed_output["emitted"] = True
            streamed_output["buffer"].append(chunk)

            text = streamed_output["line_buffer"] + chunk
            lines = text.split("\n")
            streamed_output["line_buffer"] = lines.pop()

            for line in lines:
                if _is_execute_prompt_line(line):
                    continue
                console.print(line, markup=False)

        chat_kwargs = {}
        if force_tool_confirmation is not None:
            chat_kwargs["force_tool_confirmation"] = force_tool_confirmation

        result = await handler.chat(input_text, stream_handler=stream_handler, **chat_kwargs)
        final_text = result
        if streamed_output["emitted"]:
            tail = streamed_output["line_buffer"]
            if tail and not _is_execute_prompt_line(tail):
                console.print(tail, markup=False)
            console.print()
            if not final_text:
                final_text = "".join(streamed_output["buffer"])
        elif result:
            display_result(_strip_execute_prompt_lines(result), config, quiet)
        should_suggest = force_command_oriented or _is_command_oriented_query(input_text)
        if final_text and not handler.last_tool_calls_executed and should_suggest:
            await _maybe_execute_suggested_command(
                final_text,
                config,
                quiet,
                user_query=input_text,
                command_confirmation=command_confirmation,
            )
        return

    if not quiet:
        console.print(f"[dim]Thinking with {config.get_current_model()}...[/dim]")
        chat_kwargs = {}
        if force_tool_confirmation is not None:
            chat_kwargs["force_tool_confirmation"] = force_tool_confirmation
        result = await handler.chat(input_text, **chat_kwargs)
    else:
        chat_kwargs = {}
        if force_tool_confirmation is not None:
            chat_kwargs["force_tool_confirmation"] = force_tool_confirmation
        result = await handler.chat(input_text, **chat_kwargs)

    if result:
        display_result(_strip_execute_prompt_lines(result), config, quiet)
        should_suggest = force_command_oriented or _is_command_oriented_query(input_text)
        if not handler.last_tool_calls_executed and should_suggest:
            await _maybe_execute_suggested_command(
                result,
                config,
                quiet,
                user_query=input_text,
                command_confirmation=command_confirmation,
            )


def display_result(result: str, config, quiet: bool = False):
    """Display result with appropriate formatting."""
    if not result:
        return

    if quiet:
        # Minimal output
        console.print(result, markup=False, soft_wrap=True)
    elif config.rich_output:
        # Rich formatted output
        renderable = Text(result, overflow="fold")
        console.print()
        console.print(
            Panel(
                renderable,
                title="[bold green]Result[/bold green]",
                border_style="green",
                box=box.HORIZONTALS,
            )
        )
    else:
        # Plain text output
        console.print("\n[bold green]Result:[/bold green]")
        console.print(result, markup=False, soft_wrap=True)


def handle_error(error: Exception, debug: bool = False):
    """Handle and display errors with appropriate formatting."""
    if debug:
        console.print("\n[bold red]Debug Error Details:[/bold red]")
        console.print_exception()
    else:
        console.print(f"\n[bold red]Error:[/bold red] {str(error)}")
        console.print("[dim]Use --debug for more details[/dim]")


if __name__ == "__main__":
    app()
