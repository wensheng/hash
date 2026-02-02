"""Shell command execution tool for LLM."""

import shlex
import subprocess
from typing import Any, Dict, List

from ..config import HashConfig
from .base import Tool


class ShellTool(Tool):
    """Tool for executing shell commands."""

    def get_name(self) -> str:
        return "execute_shell_command"

    def get_description(self) -> str:
        return "Execute a shell command and return its output"

    async def execute(self, arguments: Dict[str, Any], config: HashConfig) -> str:
        """Execute a shell command with security checks."""

        if not config.allow_command_execution:
            return "Command execution is disabled in configuration."

        # Extract arguments
        command = arguments.get("command", "")
        arguments.get("description", "Shell command")

        if not command:
            return "No command provided."

        # Security validation
        security_check = self._validate_command_security(command, config)
        if security_check:
            return security_check

        try:
            use_shell = self._should_use_shell(command, config)

            if use_shell:
                # Execute via shell only when explicitly enabled for operators.
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=config.command_timeout,
                    shell=True,
                    cwd=None,
                )
            else:
                # Parse command safely
                if isinstance(command, str):
                    # Use shlex to safely parse the command
                    cmd_args = shlex.split(command)
                else:
                    cmd_args = command

                # Execute command with security restrictions
                result = subprocess.run(
                    cmd_args,
                    capture_output=True,
                    text=True,
                    timeout=config.command_timeout,
                    shell=False,  # Never use shell=True for security
                    cwd=None,  # Use current directory
                )

            # Format output
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
