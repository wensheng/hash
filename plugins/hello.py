#!/usr/bin/env python3
"""Example Hash CLI plugin.

Plugins are standalone executables in any language. Install with:

    hashcli --add-cmd plugins/hello.py

Protocol:
  * `hello.py --hashcli-help` prints help text (the first line is the summary).
  * `hello.py <args...>` prints the command result to stdout.
  * HASHCLI_PLUGIN_CONFIG holds the effective configuration as JSON (API keys removed).
"""

import sys

HELP = """Say hello:
  /hello                  - Print a default greeting
  /hello <name>           - Greet a specific name

Examples:
  /hello
  /hello world"""


def main(argv):
    if argv[:1] == ["--hashcli-help"]:
        print(HELP)
        return 0
    if argv:
        print(f"Hello, {' '.join(argv).strip()}!")
    else:
        print("Hello from Hash CLI plugin!")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
