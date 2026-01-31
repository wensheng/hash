"""Main entry point for Hash CLI with dual-mode functionality."""

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path

    # Add the project root to the Python path
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    __package__ = "hashcli"


import asyncio
import os
import re
import shlex
import subprocess
from typing import Optional
from pathlib import Path
from typing import List

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm

from hashcli.command_proxy import CommandProxy
from hashcli.config import (
    ConfigurationError,
    LLMProvider,
    get_model_options,
    load_configuration,
    save_config,
    validate_api_setup,
)
from hashcli.llm_handler import LLMHandler

# Initialize Typer app with rich formatting
app = typer.Typer(
    name="hashcli",
    help="Hash - Intelligent CLI assistant with dual-mode functionality",
    add_completion=False,  # We'll handle completion in shell integration
    rich_markup_mode="rich",
    no_args_is_help=False,  # Allow no args to show usage info
)

console = Console()


def _extract_suggested_command(response_text: str, user_query: Optional[str] = None) -> Optional[str]:
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
        forbidden_operators = ["&&", "||", ";", "|", "`", "$(", ">", "<"]
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
                remainder = line[len(prefix) :].lstrip()
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
            if token.isdigit():
                filtered.append(token)
                continue
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

    def add_candidate(candidate: str, source: str, position: int = 0) -> Optional[dict]:
        cleaned = clean_command(candidate)
        if not is_probable_command(cleaned):
            return None
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

    match = re.search(r"do you want execute\s+`([^`]+)`", response_text, re.IGNORECASE)
    if match:
        item = add_candidate(match.group(1), "explicit")
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
        item = add_candidate(stripped, "bare", idx)
        if item:
            candidates.append(item)

    inline_commands = re.findall(r"`([^`]+)`", response_text)
    for idx, inline in enumerate(inline_commands):
        item = add_candidate(inline, "inline", idx)
        if item:
            candidates.append(item)

    if not candidates:
        return None

    if query_tokens:
        matched = [c for c in candidates if all(token in c["command"].lower() for token in query_tokens)]
        if matched:
            matched.sort(key=lambda c: (len(c["parts"]), len(c["command"]), c["position"]))
            return matched[0]["command"]

    candidates.sort(key=lambda c: (c["score"], -c["position"]), reverse=True)
    return candidates[0]["command"]


async def _maybe_execute_suggested_command(
    response_text: str,
    config,
    quiet: bool = False,
    user_query: Optional[str] = None,
) -> None:
    suggested_command = _extract_suggested_command(response_text, user_query=user_query)
    if not suggested_command:
        return

    question = f"do you want execute `{suggested_command}`?"
    if not Confirm.ask(question, default=False):
        return

    from hashcli.tools.shell import ShellTool

    tool = ShellTool()
    result = await tool.execute(
        {
            "command": suggested_command,
            "description": "User-confirmed command execution",
        },
        config,
    )
    if result:
        display_result(result, config, quiet)


def show_welcome():
    """Display welcome message with usage instructions."""
    welcome_text = """
# Hash CLI - Intelligent Terminal Assistant

## Usage Modes

**LLM Chat Mode** (Natural language queries):
```bash
hashcli how do I list large files?
hashcli explain this error: permission denied
hashcli help me debug this python script
```

**Command Proxy Mode** (Direct commands with `/` prefix):
```bash
hashcli /model gpt-5-mini
hashcli /clean
hashcli /fix "implement authentication"
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
        from . import __version__

        console.print(f"Hash CLI version {__version__}")
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
            console.print(f"Confirmation required: [cyan]{'Yes' if config.require_confirmation else 'No'}[/cyan]")
            console.print(f"History: [cyan]{'Enabled' if config.history_enabled else 'Disabled'}[/cyan]")
            if config.history_enabled:
                console.print(f"History location: [dim]{config.history_dir}[/dim]")
            console.print(f"Streaming: [cyan]{'Enabled' if config.streaming else 'Disabled'}[/cyan]")

        except Exception as e:
            console.print(f"[bold red]Error loading configuration:[/bold red] {e}")
            raise typer.Exit(1)
        raise typer.Exit()


def config_callback(value: bool):
    """Run interactive setup wizard and exit."""
    if value:
        console.print("\n[bold blue]Hash CLI Setup Wizard[/bold blue]")
        console.print("This will help you configure Hash CLI for first use.\n")

        # Provider selection
        console.print("[bold]1. Choose your LLM provider:[/bold]")
        console.print("   1) OpenAI (GPT-4, GPT-3.5)")
        console.print("   2) Anthropic (Claude)")
        console.print("   3) Google (Gemini)")

        provider_choice = typer.prompt("Enter choice (1-3)", type=int)

        if provider_choice == 1:
            provider = LLMProvider.OPENAI
            console.print("\n[bold]2. Get your OpenAI API key:[/bold]")
            console.print("   Visit: https://platform.openai.com/api-keys")
        elif provider_choice == 2:
            provider = LLMProvider.ANTHROPIC
            console.print("\n[bold]2. Get your Anthropic API key:[/bold]")
            console.print("   Visit: https://console.anthropic.com/")
        elif provider_choice == 3:
            provider = LLMProvider.GOOGLE
            console.print("\n[bold]2. Get your Google AI API key:[/bold]")
            console.print("   Visit: https://makersuite.google.com/app/apikey")
        else:
            console.print("[red]Invalid choice[/red]")
            raise typer.Exit(1)

        # Select Model
        console.print(f"\n[bold]2. Select {provider.value.capitalize()} Model:[/bold]")
        options = get_model_options(provider)
        for i, model in enumerate(options, 1):
            console.print(f"   {i}) {model}")
        console.print(f"   {len(options) + 1}) Custom model name")

        model_choice = typer.prompt(f"Select model (1-{len(options)+1})", type=int, default=1)

        if 1 <= model_choice <= len(options):
            selected_model = options[model_choice - 1]
        elif model_choice == len(options) + 1:
            selected_model = typer.prompt("Enter custom model name")
        else:
            console.print("[red]Invalid choice[/red]")
            raise typer.Exit(1)

        # API key input
        api_key = typer.prompt("\nEnter your API key", hide_input=True)

        # Save API key to config
        if provider == LLMProvider.OPENAI:
            config = load_configuration()
            config.openai_model = selected_model
            config.openai_api_key = api_key
            save_config(config)
        elif provider == LLMProvider.ANTHROPIC:
            config = load_configuration()
            config.anthropic_model = selected_model
            config.anthropic_api_key = api_key
            save_config(config)
        elif provider == LLMProvider.GOOGLE:
            config = load_configuration()
            config.google_model = selected_model
            config.google_api_key = api_key
            save_config(config)

        console.print("\n[bold green]Setup complete![/bold green]")
        console.print("API key has been saved to ~/.hashcli/config.toml")
        console.print("Try: [code]hashcli hello world[/code]")
        raise typer.Exit()


def setup_callback(value: bool):
    """Install shell integration and exit."""
    if not value:
        return

    shell_env = os.environ.get("SHELL", "")
    shell_name = Path(shell_env).name

    # Support zsh and bash
    if shell_name not in ("zsh", "bash"):
        console.print("[yellow]Shell integration setup currently supports zsh and bash only.[/yellow]")
        console.print(f"Detected shell: [dim]{shell_name or 'unknown'}[/dim]")
        console.print("For other shells, use the scripts in the shell directory.")
        raise typer.Exit(1)

    # Copy shell scripts to ~/.hashcli/shell/ if not already there
    user_shell_dir = Path.home() / ".hashcli" / "shell"
    install_script = user_shell_dir / shell_name / "install.sh"

    if not install_script.exists():
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
                console.print(f"[dim]Shell scripts copied to {user_shell_dir}[/dim]")
            else:
                console.print("[bold red]Unable to locate shell scripts in package.[/bold red]")
                raise typer.Exit(1)
        except Exception as e:
            console.print(f"[bold red]Failed to copy shell scripts:[/bold red] {e}")
            raise typer.Exit(1)

    # Run install script from user directory
    console.print(f"[bold blue]Installing {shell_name} shell integration...[/bold blue]")
    try:
        subprocess.run(
            ["/bin/bash", str(install_script), "install"],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        console.print("[bold red]Shell integration setup failed.[/bold red]")
        raise typer.Exit(exc.returncode) from exc

    raise typer.Exit()


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
    config_file: Optional[str] = typer.Option(None, "--config-file", "-c", help="Path to custom configuration file"),
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
        help="Override LLM provider (openai, anthropic, google)",
    ),
    no_confirm: bool = typer.Option(False, "--no-confirm", "-y", help="Skip confirmation prompts for tool calls"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimize output, show only results"),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show version information and exit.",
    ),
    show_config: Optional[bool] = typer.Option(
        None,
        "--show-config",
        callback=show_config_callback,
        is_eager=True,
        help="Show current configuration and exit.",
    ),
    setup: Optional[bool] = typer.Option(
        None,
        "--setup",
        callback=setup_callback,
        is_eager=True,
        help="Install shell integration and exit.",
    ),
    configure: Optional[bool] = typer.Option(
        None,
        "--config",
        callback=config_callback,
        is_eager=True,
        help="Interactively configure model provider and model.",
    ),
):
    """Main function for the Hash CLI."""
    if not query:
        show_welcome()
        raise typer.Exit()

    input_text = " ".join(query)

    try:
        config = load_configuration(
            config_file=config_file,
            debug=debug,
            model_override=model,
        )

        # Update config with CLI options
        if provider:
            config.llm_provider = LLMProvider(provider)
        if no_confirm:
            config.require_confirmation = False

        # Validate API key setup
        validate_api_setup(config)

        # Decide execution mode
        if input_text.startswith("/"):
            execute_command_mode(input_text, config, quiet)
        else:
            asyncio.run(execute_llm_mode(input_text, config, quiet))

    except ConfigurationError as e:
        handle_error(e, debug)
        console.print("\n[bold]Tip:[/bold] Run `hashcli --setup` to get started.")
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


async def execute_llm_mode(input_text: str, config, quiet: bool = False):
    """Execute query in LLM chat mode."""
    handler = LLMHandler(config)

    if config.streaming:
        streamed_output = {"emitted": False, "buffer": []}

        def stream_handler(chunk: str) -> None:
            if not chunk:
                return
            streamed_output["emitted"] = True
            streamed_output["buffer"].append(chunk)
            console.print(chunk, end="", markup=False)

        result = await handler.chat(input_text, stream_handler=stream_handler)
        final_text = result
        if streamed_output["emitted"]:
            console.print()
            if not final_text:
                final_text = "".join(streamed_output["buffer"])
        elif result:
            display_result(result, config, quiet)
        if final_text and not handler.last_tool_calls_executed:
            await _maybe_execute_suggested_command(final_text, config, quiet, user_query=input_text)
        return

    if not quiet:
        with console.status(f"[dim]Thinking with {config.get_current_model()}...[/dim]"):
            result = await handler.chat(input_text)
    else:
        result = await handler.chat(input_text)

    if result:
        display_result(result, config, quiet)
        if not handler.last_tool_calls_executed:
            await _maybe_execute_suggested_command(result, config, quiet, user_query=input_text)


def display_result(result: str, config, quiet: bool = False):
    """Display result with appropriate formatting."""
    if not result:
        return

    if quiet:
        # Minimal output
        console.print(result)
    elif config.rich_output:
        # Rich formatted output
        console.print()
        console.print(Panel(result, title="[bold green]Result[/bold green]", border_style="green"))
    else:
        # Plain text output
        console.print("\n[bold green]Result:[/bold green]")
        console.print(result)


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
