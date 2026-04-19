"""Shell command execution tool for LLM."""

import re
import shlex
import subprocess
from typing import Any, Dict, List, Optional, Sequence

from ..config import HashConfig
from .base import Tool


class ShellTool(Tool):
    """Tool for executing shell commands."""

    _DESTRUCTIVE_COMMAND_PATTERNS = (
        r"\bkill(?:all)?\b",
        r"\bpkill\b",
        r"\bfuser\b[^\n\r]*\s-k\b",
        r"\blsof\b[^\n\r]*\|\s*xargs\b[^\n\r]*\bkill\b",
        r"\btaskkill\b",
        r"\bstop-process\b",
        r"\bremove-item\b",
        r"\brm\b",
        r"\bdel\b",
        r"\bmv\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\btruncate\b",
        r"\bmkfs\b",
        r"\bdd\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r"\binit\s+[06]\b",
        r"\blaunchctl\s+(?:unload|remove|bootout)\b",
        r"\bsystemctl\s+(?:stop|restart|kill|disable|mask)\b",
        r"\bservice\s+\S+\s+(?:stop|restart)\b",
        r"\bdocker\s+(?:rm|rmi|stop|kill)\b",
        r"\bkubectl\s+delete\b",
        r"\bgit\s+reset\b",
        r"\bgit\s+clean\b",
        r"\bbrew\s+uninstall\b",
        r"\bapt(?:-get)?\s+remove\b",
        r"\byum\s+remove\b",
        r"\bdnf\s+remove\b",
        r"\bpacman\s+-R\b",
    )

    def get_name(self) -> str:
        return "execute_shell_command"

    def get_description(self) -> str:
        return "Execute a shell command and return its output"

    @classmethod
    def is_potentially_destructive_command(cls, command: Any) -> bool:
        """Return True when the command can stop processes or mutate the system."""
        if not isinstance(command, str):
            return False

        normalized = command.strip().lower()
        if not normalized:
            return False

        return any(re.search(pattern, normalized) for pattern in cls._DESTRUCTIVE_COMMAND_PATTERNS)

    async def execute(self, arguments: Dict[str, Any], config: HashConfig) -> str:
        """Execute a shell command with security checks."""

        if not config.allow_command_execution:
            return "Command execution is disabled in configuration."

        # Extract arguments
        command = arguments.get("command", "")
        arguments.get("description", "Shell command")
        passthrough_output = bool(arguments.get("passthrough_output", False))

        if not command:
            return "No command provided."

        # Security validation
        security_check = self._validate_command_security(command, config)
        if security_check:
            return security_check

        try:
            return self._run_command(command, config, passthrough_output=passthrough_output)

        except subprocess.TimeoutExpired:
            return f"Command timed out after {config.command_timeout} seconds"
        except subprocess.CalledProcessError as e:
            return f"Command failed with exit code {e.returncode}: {e}"
        except FileNotFoundError:
            if isinstance(command, str):
                try:
                    cmd_preview = shlex.split(command)
                except ValueError:
                    cmd_preview = []
            else:
                cmd_preview = command
            return f"Command not found: {cmd_preview[0] if cmd_preview else 'unknown'}"
        except Exception as e:
            return f"Error executing command: {e}"

    def _run_command(
        self,
        command: Any,
        config: HashConfig,
        passthrough_output: bool = False,
    ) -> str:
        """Run a validated command and optionally stream child stdio directly to the user."""

        use_shell = self._should_use_shell(command, config)
        cmd_args: Optional[Sequence[str]] = None

        if not use_shell:
            if isinstance(command, str):
                # Use shlex to safely parse the command
                cmd_args = shlex.split(command)
            else:
                cmd_args = command

        run_kwargs: Dict[str, Any] = {
            "text": True,
            "timeout": config.command_timeout,
            "shell": use_shell,
            "cwd": None,
        }

        if passthrough_output:
            result = subprocess.run(command if use_shell else cmd_args, **run_kwargs)
            if result.returncode != 0:
                return f"Exit code: {result.returncode}"
            return ""

        result = subprocess.run(
            command if use_shell else cmd_args,
            capture_output=True,
            **run_kwargs,
        )

        output = ""
        if result.stdout:
            output += result.stdout

        if result.stderr:
            if output:
                output += "\n\n"
            output += f"stderr:\n{result.stderr}"

        if result.returncode != 0:
            if output:
                output += "\n\n"
            output += f"Exit code: {result.returncode}"

        if not output:
            output = f"Command completed successfully (exit code: {result.returncode})"

        return output.strip()

    def _validate_command_security(self, command: str, config: HashConfig) -> str:
        """Validate command against security policies."""

        # Check against blocked commands
        command_lower = command.lower()
        for blocked in config.blocked_commands:
            if blocked.lower() in command_lower:
                return f"Blocked command detected: {blocked}"

        # Check against allowed commands if whitelist is configured
        if config.allowed_commands:
            if isinstance(command, str):
                base_commands = self._extract_base_commands(command)
            else:
                base_commands = command[:1] if command else []

            for base_cmd in base_commands:
                if base_cmd not in config.allowed_commands:
                    return f"Command not in allowed list: {base_cmd}"

        # Additional security checks
        dangerous_patterns = [
            "$(",
            "`",
            "&&",
            "||",
            "rm -rf /",
            "sudo rm",
            "chmod 777",  # Dangerous file operations
            "curl | sh",
            "wget | sh",  # Dangerous download-execute patterns
        ]

        if not config.allow_shell_operators:
            dangerous_patterns.extend([";", "|"])  # Command injection patterns

        for pattern in dangerous_patterns:
            if pattern in command_lower:
                return f"Potentially dangerous command pattern detected: {pattern}"

        return ""  # Command is valid

    def _should_use_shell(self, command: Any, config: HashConfig) -> bool:
        if not config.allow_shell_operators:
            return False
        if not isinstance(command, str):
            return False
        return "|" in command or ";" in command

    def _extract_base_commands(self, command: str) -> List[str]:
        """Extract base commands from a command string, honoring pipe/semicolon separators."""
        segments = self._split_command_chain(command)
        base_commands = []
        for segment in segments:
            try:
                parts = shlex.split(segment)
            except ValueError:
                continue
            if parts:
                base_commands.append(parts[0])
        return base_commands

    def _split_command_chain(self, command: str) -> List[str]:
        """Split a command string on unquoted | and ; characters."""
        segments = []
        current = []
        in_single = False
        in_double = False
        escaped = False

        for ch in command:
            if escaped:
                current.append(ch)
                escaped = False
                continue

            if ch == "\\" and not in_single:
                escaped = True
                current.append(ch)
                continue

            if ch == "'" and not in_double:
                in_single = not in_single
                current.append(ch)
                continue

            if ch == '"' and not in_single:
                in_double = not in_double
                current.append(ch)
                continue

            if not in_single and not in_double and ch in ("|", ";"):
                segment = "".join(current).strip()
                if segment:
                    segments.append(segment)
                current = []
                continue

            current.append(ch)

        segment = "".join(current).strip()
        if segment:
            segments.append(segment)

        return segments

    def requires_confirmation(self) -> bool:
        """Shell commands always require confirmation for safety."""
        return True
