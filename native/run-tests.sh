#!/usr/bin/env bash
# Run the native integration's tests without Home Assistant installed.
#
# scanner.py is stdlib-only by design, so it is testable standalone. llm.py needs
# the Home Assistant package and is validated by Core loading it instead.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PYTHONPATH="${PWD}:${PYTHONPATH:-}" python3 -m unittest discover -s tests -v
