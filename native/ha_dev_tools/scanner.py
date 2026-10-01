"""Bounded, read-only scan of the Home Assistant configuration directory.

A registry-only rename changes an entity's ID and nothing else, so every
automation, script, scene and dashboard that names the old ID is left pointing
at something that no longer exists. Reporting *which files* name it is the
difference between a rename that is planned and one that is discovered to be
broken three days later.

This module is deliberately pure: it takes a configuration directory and returns
a report. It does not import Home Assistant, hold state, or know about entities.
``llm.py`` is the thin Home Assistant layer on top.

Everything here fails soft. A directory that cannot be read is a missing
convenience reported as ``scanned: False``, never an exception, because this is
advisory information attached to someone else's decision.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Pathological-file guard: no configuration file needs more than this.
MAX_SCAN_BYTES = 8 * 1024 * 1024

#: Deep enough for dashboards/, packages/ and esphome/; shallow enough to stay cheap.
MAX_DEPTH = 4

#: Caps that keep a tool result readable no matter how large the config is.
MAX_FILES_SCANNED = 750
MAX_REPORTED_FILES = 25
MAX_REPORTED_LINES = 5

#: How many entity IDs one call may ask about. The scan runs once per ID, so an
#: unbounded list would turn a tool call into a denial of service against yourself.
MAX_ENTITY_IDS = 25

#: Formats an entity ID can legitimately appear in inside YAML/JSON configuration.
SCANNED_EXTENSIONS = frozenset({".yaml", ".yml", ".json"})

#: Never read: credential files, Home Assistant internals, dependency trees, and
#: HACS-managed code the user has asked not to be touched.
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".storage",
        ".cloud",
        ".git",
        "deps",
        "tts",
        "node_modules",
        "__pycache__",
        "custom_components",
        "www",
    }
)

#: ``secrets.yaml`` is excluded on principle — a match count never justifies reading it.
EXCLUDED_FILES = frozenset({"secrets.yaml"})

ENTITY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9][a-z0-9_]*$")


def is_valid_entity_id(entity_id: object) -> bool:
    """Return True if `entity_id` is a well-formed Home Assistant entity ID."""
    return isinstance(entity_id, str) and ENTITY_ID_PATTERN.match(entity_id) is not None


def _reference_pattern(entity_id: str) -> re.Pattern[str]:
    """Match a whole entity ID, not a fragment of one.

    ``sensor.front_door`` occurs inside ``binary_sensor.front_door``, and
    ``lock.front_door`` occurs inside ``lock.front_door_extra``. Reporting either
    would fill the result with entities that are not actually referenced, which
    teaches the reader to ignore the list.
    """
    return re.compile(rf"(?<![A-Za-z0-9_.]){re.escape(entity_id)}(?![A-Za-z0-9_])")


def _unscanned(reason: str) -> dict[str, object]:
    return {
        "scanned": False,
        "reason": reason,
        "files_scanned": 0,
        "file_count": 0,
        "total_matches": 0,
        "files": [],
        "truncated": False,
    }


def _is_excluded_directory(name: str) -> bool:
    return name.startswith(".") or name in EXCLUDED_DIRECTORIES


def _is_scannable_file(name: str) -> bool:
    if name in EXCLUDED_FILES or name.startswith("."):
        return False
    return Path(name).suffix.lower() in SCANNED_EXTENSIONS


def _matching_lines(text: str, pattern: re.Pattern[str]) -> tuple[int, list[int]]:
    """Return the match count and up to MAX_REPORTED_LINES 1-based line numbers."""
    count = 0
    lines: list[int] = []
    for index, line in enumerate(text.splitlines(), start=1):
        found = len(pattern.findall(line))
        if not found:
            continue
        count += found
        if len(lines) < MAX_REPORTED_LINES:
            lines.append(index)
    return count, lines


def find_entity_references(entity_id: str, config_dir: str) -> dict[str, object]:
    """Report the configuration files that name `entity_id`.

    Never raises. Returns a report whose ``scanned`` key says whether the search
    actually ran, so a caller can distinguish "nothing references this" from
    "we could not look".
    """
    if not is_valid_entity_id(entity_id):
        return _unscanned("not a valid entity ID")

    root = Path(config_dir)
    if not root.is_dir():
        return _unscanned("configuration directory is not readable")

    pattern = _reference_pattern(entity_id)
    matches: list[dict[str, object]] = []
    files_scanned = 0
    total_matches = 0
    truncated = False

    def visit(directory: Path, depth: int) -> None:
        nonlocal files_scanned, total_matches, truncated
        if files_scanned >= MAX_FILES_SCANNED:
            truncated = True
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            return

        for entry in entries:
            if files_scanned >= MAX_FILES_SCANNED:
                truncated = True
                return

            name = entry.name
            try:
                # follow_symlinks=False throughout: a symlink must not pull a file
                # in from outside the configuration directory.
                if entry.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH and not _is_excluded_directory(name):
                        visit(Path(entry.path), depth + 1)
                    continue
                if not entry.is_file(follow_symlinks=False) or not _is_scannable_file(name):
                    continue
                if entry.stat(follow_symlinks=False).st_size > MAX_SCAN_BYTES:
                    continue
                files_scanned += 1
                with open(entry.path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue

            count, lines = _matching_lines(text, pattern)
            if not count:
                continue

            total_matches += count
            if len(matches) < MAX_REPORTED_FILES:
                matches.append(
                    {
                        "path": os.path.relpath(entry.path, root),
                        "count": count,
                        "lines": lines,
                    }
                )
            else:
                truncated = True

    try:
        visit(root, 0)
    except OSError:
        # A walk failure must not fail the report; return what was collected.
        pass

    return {
        "scanned": True,
        "files_scanned": files_scanned,
        "file_count": len(matches),
        "total_matches": total_matches,
        "files": matches,
        "truncated": truncated,
    }
