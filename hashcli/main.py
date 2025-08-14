"""Main entry point for Hash CLI with dual-mode functionality."""

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path

    # Add the project root to the Python path
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    __package__ = "hashcli"


import asyncio
from typing import List, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from hashcli.command_proxy import CommandProxy
from hashcli.config import (
    ConfigurationError,
    load_configuration,
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
hashcli /ls -la
hashcli /model gpt-5-mini
hashcli /clear
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
            console.print(
                f"API Key: [green]{'✓ Set' if config.get_current_api_key() else '✗ Not set'}[/green]"
            )
            console.print(
                f"Command execution: [cyan]{'Enabled' if config.allow_command_execution else 'Disabled'}[/cyan]"
            )
            console.print(
                f"Confirmation required: [cyan]{'Yes' if config.require_confirmation else 'No'}[/cyan]"
            )
            console.print(
                f"History: [cyan]{'Enabled' if config.history_enabled else 'Disabled'}[/cyan]"
            )
            if config.history_enabled:
                console.print(f"History location: [dim]{config.history_dir}[/dim]")

        except Exception as e:
            console.print(f"[bold red]Error loading configuration:[/bold red] {e}")
            raise typer.Exit(1)
        raise typer.Exit()


def setup_callback(value: bool):
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
            provider = "openai"
            console.print("\n[bold]2. Get your OpenAI API key:[/bold]")
            console.print("   Visit: https://platform.openai.com/api-keys")
        elif provider_choice == 2:
            provider = "anthropic"
            console.print("\n[bold]2. Get your Anthropic API key:[/bold]")
            console.print("   Visit: https://console.anthropic.com/")
        elif provider_choice == 3:
            provider = "google"
            console.print("\n[bold]2. Get your Google AI API key:[/bold]")
            console.print("   Visit: https://makersuite.google.com/app/apikey")
        else:
            console.print("[red]Invalid choice[/red]")
            raise typer.Exit(1)

        # API key input
        api_key = typer.prompt("\nEnter your API key", hide_input=True)

        # Set environment variable suggestion
        env_var = f"{provider.upper()}_API_KEY"
        console.print(f"\n[bold]3. Save your API key:[/bold]")
        console.print(f"   Add this to your shell profile (.bashrc, .zshrc, etc.):")
        console.print(f'   [code]export {env_var}="{api_key[:8]}..."[/code]')
        console.print(
            "   Then reload your terminal or run: [code]source ~/.bashrc[/code]"
        )

        console.print("\n[bold green]Setup complete![/bold green]")
        console.print("Try: [code]hashcli hello world[/code]")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    query: List[str] = typer.Argument(
        None, help="Query or command to execute. Use /command for proxy mode."
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="Enable debug output and detailed error information",
    ),
    config_file: Optional[str] = typer.Option(
        None, "--config-file", "-c", help="Path to custom configuration file"
    ),
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
    no_confirm: bool = typer.Option(
        False, "--no-confirm", "-y", help="Skip confirmation prompts for tool calls"
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Minimize output, show only results"
    ),
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
        help="Run the interactive setup wizard and exit.",
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
    if not quiet:
        with console.status(
            f"[dim]Thinking with {config.get_current_model()}...[/dim]"
        ):
            handler = LLMHandler(config)
            result = await handler.chat(input_text)
    else:
        handler = LLMHandler(config)
        result = await handler.chat(input_text)

    if result:
        display_result(result, config, quiet)


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
        console.print(
            Panel(result, title="[bold green]Result[/bold green]", border_style="green")
        )
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
