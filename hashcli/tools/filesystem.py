"""File system operations tool for LLM."""

import os
from pathlib import Path
from typing import Any, Dict

from ..config import HashConfig
from .base import Tool


class FileSystemTool(Tool):
    """Tool for file system operations."""

    def get_name(self) -> str:
        return "filesystem_operations"

    def get_description(self) -> str:
        return "Perform file system operations like read, write, and list files"

    async def execute(self, arguments: Dict[str, Any], config: HashConfig) -> str:
        """Execute file system operation based on the function name from LLM."""

        # The actual function name is passed as part of the tool call
        # We need to determine which operation to perform
        if "file_path" in arguments and "content" in arguments:
            return await self._write_file(arguments, config)
        elif "file_path" in arguments:
            return await self._read_file(arguments, config)
        elif "directory_path" in arguments:
            return await self._list_directory(arguments, config)
        else:
            return "Invalid file system operation arguments"

    async def _read_file(self, arguments: Dict[str, Any], config: HashConfig) -> str:
        """Read a file and return its contents."""
        file_path = arguments.get("file_path", "")

        if not file_path:
            return "No file path provided"

        try:
            path = Path(file_path).resolve()

            # Security check: prevent reading sensitive files
            if self._is_sensitive_file(path):
                return f"Access to sensitive file denied: {path}"

            # Check if file exists
            if not path.exists():
                return f"File does not exist: {path}"

            if not path.is_file():
                return f"Path is not a file: {path}"

            # Read file with size limit for safety
            max_size = 1024 * 1024  # 1MB limit
            if path.stat().st_size > max_size:
                return f"File too large to read (max 1MB): {path}"

            # Read file content
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            return f"Content of {path}:\\n\\n{content}"

        except UnicodeDecodeError:
            return f"File appears to be binary: {path}"
        except PermissionError:
            return f"Permission denied reading file: {path}"
        except Exception as e:
            return f"Error reading file: {e}"

    async def _write_file(self, arguments: Dict[str, Any], config: HashConfig) -> str:
        """Write content to a file."""
        file_path = arguments.get("file_path", "")
        content = arguments.get("content", "")

        if not file_path:
            return "No file path provided"

        try:
            path = Path(file_path).resolve()

            # Security check: prevent writing to sensitive locations
            if self._is_sensitive_path(path):
                return f"Access to sensitive path denied: {path}"

            # Create parent directories if they don't exist
            path.parent.mkdir(parents=True, exist_ok=True)

            # Write file content
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            return f"Successfully wrote {len(content)} characters to {path}"

        except PermissionError:
            return f"Permission denied writing to file: {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    async def _list_directory(
        self, arguments: Dict[str, Any], config: HashConfig
    ) -> str:
        """List contents of a directory."""
        directory_path = arguments.get("directory_path", ".")
        show_hidden = arguments.get("show_hidden", False)

        try:
            path = Path(directory_path).resolve()

            # Security check
            if self._is_sensitive_path(path):
                return f"Access to sensitive path denied: {path}"

            # Check if directory exists
            if not path.exists():
                return f"Directory does not exist: {path}"

            if not path.is_dir():
                return f"Path is not a directory: {path}"

            # List directory contents
            entries = []
            try:
                for entry in path.iterdir():
                    if not show_hidden and entry.name.startswith("."):
                        continue

                    entry_type = "DIR" if entry.is_dir() else "FILE"
                    size = ""
                    if entry.is_file():
                        try:
                            file_size = entry.stat().st_size
                            if file_size < 1024:
                                size = f"({file_size}B)"
                            elif file_size < 1024 * 1024:
                                size = f"({file_size/1024:.1f}KB)"
                            else:
                                size = f"({file_size/(1024*1024):.1f}MB)"
                        except:
                            size = "(unknown size)"

                    entries.append(f"{entry_type:<4} {entry.name} {size}")

            except PermissionError:
                return f"Permission denied listing directory: {path}"

            if not entries:
                return f"Directory is empty: {path}"

            result = f"Contents of {path}:\\n\\n"
            result += "\\n".join(sorted(entries))

            return result

        except Exception as e:
            return f"Error listing directory: {e}"

    def _is_sensitive_file(self, path: Path) -> bool:
        """Check if a file path is sensitive and should not be read."""
        sensitive_patterns = [
            "/etc/passwd",
            "/etc/shadow",
            "/etc/sudoers",
            ".ssh/id_rsa",
            ".ssh/id_dsa",
            ".ssh/id_ecdsa",
            ".ssh/id_ed25519",
            ".aws/credentials",
            ".env",
            ".secret",
            "private_key",
            "id_rsa",
            "id_dsa",
            "id_ecdsa",
            "id_ed25519",
        ]

        path_str = str(path).lower()
        return any(pattern in path_str for pattern in sensitive_patterns)

    def _is_sensitive_path(self, path: Path) -> bool:
        """Check if a path is sensitive and should not be written to."""
        sensitive_paths = [
            "/etc",
            "/bin",
            "/sbin",
            "/usr/bin",
            "/usr/sbin",
            "/sys",
            "/proc",
            "/dev",
            "/boot",
            "C:\\\\Windows",
            "C:\\\\Program Files",
            "C:\\\\System32",
        ]

        path_str = str(path)
        return any(path_str.startswith(sensitive) for sensitive in sensitive_paths)

    def requires_confirmation(self) -> bool:
        """File operations require confirmation when modifying files."""
        return True
