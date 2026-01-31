"""TLDR command implementation for quick command examples."""

import subprocess
import sys
from typing import List

from ..command_proxy import Command
from ..config import HashConfig


class TLDRCommand(Command):
    """Command to show TLDR pages via the bundled tldr client."""

    def execute(self, args: List[str], config: HashConfig) -> str:
        """Execute the TLDR lookup using the internal tldr module."""
        cmd_args = [sys.executable, "-m", "hashcli.commands._tldr", *args]

        try:
            result = subprocess.run(
                cmd_args,
                capture_output=True,
                text=True,
                timeout=config.command_timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return f"tldr timed out after {config.command_timeout} seconds"
        except FileNotFoundError:
            return "Python executable not found for tldr command."
        except Exception as exc:  # pragma: no cover - defensive
            return f"tldr execution error: {exc}"

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if stdout:
            return stdout
        if stderr:
            return f"stderr: {stderr}"
        if result.returncode != 0:
            return f"tldr failed with exit code {result.returncode}"
        return "No output from tldr."

    def get_help(self) -> str:
        """Get help text for the tldr command."""
        return """Show TLDR pages for a command:
  /tldr <command>          - Show quick examples for a command
  /tldr --list             - List available commands
  /tldr --search <term>    - Search commands

Examples:
  /tldr tar
  /tldr --search docker"""
