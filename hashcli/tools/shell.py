"""Shell command execution tool for LLM."""

import shlex
import subprocess
from typing import Any, Dict

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
        description = arguments.get("description", "Shell command")

        if not command:
            return "No command provided."

        # Security validation
        security_check = self._validate_command_security(command, config)
        if security_check:
            return security_check

        try:
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
                output += f"stdout:\\n{result.stdout}"

            if result.stderr:
                if output:
                    output += "\\n\\n"
                output += f"stderr:\\n{result.stderr}"

            if result.returncode != 0:
                if output:
                    output += "\\n\\n"
                output += f"Exit code: {result.returncode}"

            if not output:
                output = (
                    f"Command completed successfully (exit code: {result.returncode})"
                )

            return output.strip()

        except subprocess.TimeoutExpired:
            return f"Command timed out after {config.command_timeout} seconds"
        except subprocess.CalledProcessError as e:
            return f"Command failed with exit code {e.returncode}: {e}"
        except FileNotFoundError:
            return f"Command not found: {cmd_args[0] if cmd_args else 'unknown'}"
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
            # Extract base command
            cmd_parts = shlex.split(command) if isinstance(command, str) else command
            if cmd_parts:
                base_cmd = cmd_parts[0]
                if base_cmd not in config.allowed_commands:
                    return f"Command not in allowed list: {base_cmd}"

        # Additional security checks
        dangerous_patterns = [
            "$(",
            "`",
            "&&",
            "||",
            ";",
            "|",  # Command injection patterns
            "rm -rf /",
            "sudo rm",
            "chmod 777",  # Dangerous file operations
            "curl | sh",
            "wget | sh",  # Dangerous download-execute patterns
        ]

        for pattern in dangerous_patterns:
            if pattern in command_lower:
                return f"Potentially dangerous command pattern detected: {pattern}"

        return ""  # Command is valid

    def requires_confirmation(self) -> bool:
        """Shell commands always require confirmation for safety."""
        return True
