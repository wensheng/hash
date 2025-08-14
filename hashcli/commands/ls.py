"""LS command implementation for cross-platform directory listing."""

import os
import platform
from pathlib import Path
from typing import List

from ..command_proxy import SystemCommand
from ..config import HashConfig


class LSCommand(SystemCommand):
    """Cross-platform directory listing command."""

    def execute(self, args: List[str], config: HashConfig) -> str:
        """Execute ls/dir command based on platform."""

        # Determine base command based on platform
        if platform.system() == "Windows":
            base_cmd = ["dir"]
            # Convert common ls flags to dir equivalents
            converted_args = self._convert_ls_args_to_dir(args)
        else:
            base_cmd = ["ls"]
            converted_args = args

        # Add default args if none provided
        if not converted_args:
            if platform.system() == "Windows":
                converted_args = ["/W"]  # Wide format
            else:
                converted_args = ["-la"]  # Long format with hidden files

        # Execute the command
        cmd_args = base_cmd + converted_args
        return self.execute_system_command(cmd_args, config)

    def _convert_ls_args_to_dir(self, args: List[str]) -> List[str]:
        """Convert common ls arguments to Windows dir equivalents."""
        converted = []

        for arg in args:
            if arg == "-l":
                # Long format
                pass  # dir shows detailed info by default
            elif arg == "-a":
                # Show hidden files
                converted.append("/AH")
            elif arg == "-la" or arg == "-al":
                # Long format with hidden files
                converted.append("/AH")
            elif arg == "-h":
                # Human readable sizes (not directly supported)
                pass
            elif arg.startswith("-"):
                # Skip other Unix flags that don't translate
                continue
            else:
                # Path or other argument
                converted.append(arg)

        return converted if converted else ["/W"]

    def get_help(self) -> str:
        """Get help text for the ls command."""
        if platform.system() == "Windows":
            return """List directory contents (Windows dir):
  /ls [path]           - List files in current or specified directory
  /ls /AH [path]       - Include hidden files
  
Examples:
  /ls                  - List current directory
  /ls C:\\Users         - List specified directory
  /ls /AH              - List with hidden files"""
        else:
            return """List directory contents:
  /ls [options] [path] - List files in current or specified directory
  
Common options:
  -l                   - Long format
  -a                   - Include hidden files
  -la                  - Long format with hidden files
  -h                   - Human readable sizes
  
Examples:
  /ls                  - List current directory (default: -la)
  /ls -l /home         - Long format for /home
  /ls -la              - Long format with hidden files"""
