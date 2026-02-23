"""Example third-party slash-command plugin for Hash CLI."""

from typing import List

from hashcli.command_proxy import Command


class HelloCommand(Command):
    """Simple greeting command installed via --add-cmd."""

    def execute(self, args: List[str]) -> str:
        if args:
            target = " ".join(args).strip()
            return f"Hello, {target}!"
        return "Hello from Hash CLI plugin!"

    def get_help(self) -> str:
        return """Say hello:
  /hello                  - Print a default greeting
  /hello <name>           - Greet a specific name

Examples:
  /hello
  /hello world"""
