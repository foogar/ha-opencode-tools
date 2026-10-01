"""Find configuration references to entity IDs that do not exist.

``scanner`` answers "what names this entity?". This module answers the inverse:
"what names an entity that isn't there?". The two failures are disjoint. A
rename leaves working references behind; a typo, a hand-edited ID, or a
``.bak``-era identifier leaves a reference with nothing behind it, and nothing
in the configuration is invalid, so Home Assistant loads it silently and the
automation simply never fires.

The critical input is ``existing``, and it must come from the *state machine*,
not the entity registry. A sensor declared in ``configuration.yaml`` under a
platform with no ``unique_id`` — for example an MQTT sensor — exists in
``hass.states`` and never appears in the entity registry at all. Validating
against the registry therefore reports real, working entities as missing, and
acting on that report deletes working configuration. ``references`` performs no
lookup of its own: the caller supplies the set of IDs that actually exist.

Service names share the ``domain.object`` shape with entity IDs, so
``light.turn_on`` and ``notify.mobile_app_some_phone`` are indistinguishable
from entities by form alone. ``service_names`` disambiguates them; without it
every service call in the configuration reads as a broken reference.

This module is deliberately pure — stdlib only, no Home Assistant import, no
state. ``llm.py`` gathers the live entities and services and hands them over.
It reuses the bounded walk and exclusion rules from ``scanner`` so both tools
see the same files; that dependency is one-way, and ``scanner`` must never
import this module.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .scanner import (
    EXCLUDED_DIRECTORIES,
    EXCLUDED_FILES,
    MAX_DEPTH,
    MAX_FILES_SCANNED,
    MAX_SCAN_BYTES,
    SCANNED_EXTENSIONS,
    is_valid_entity_id,
)

#: How many distinct missing entity IDs one call may report. The scan is a single
#: walk, so this only bounds the result, not the work.
MAX_REPORTED_ENTITIES = 60

#: How many locations one missing entity may contribute. A typo pasted into six
#: automations is one bug with six symptoms, and one entry is the useful answer.
MAX_LOCATIONS_PER_ENTITY = 10

#: Permissive on purpose: a false negative hides a real break, while a false
#: positive is one line of context away from being dismissed. The two filters
#: below remove the false-positive classes that actually occur, and
#: ``is_valid_entity_id`` confirms the shape Home Assistant itself would accept.
#:
#: The trailing ``(?!\.\d)`` is the version-string guard. ``v1.2.3`` matches
#: ``v1.2`` otherwise, and rejecting every object_id that starts with a digit
#: instead would hide real entities like ``sensor.2nd_floor``. Requiring a digit
#: after a further dot identifies the version case without that cost.
CANDIDATE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.\-])([a-z][a-z0-9_]*)\.([a-z0-9][a-z0-9_]*)(?!\.\d)"
)

#: ``workshop.yaml`` and ``my.file.name`` are references to files, not entities.
#: Without this a single dotted filename in a comment reads as a missing entity.
FILE_LIKE_SUFFIXES = frozenset(
    {
        "yaml", "yml", "json", "py", "js", "mjs", "ts", "md", "txt", "log",
        "sh", "conf", "cfg", "ini", "bak", "png", "jpg", "jpeg", "gif", "html",
    }
)

#: A line carrying a Jinja delimiter is template source, not configuration. Its
#: ``domain.object`` tokens are template variables — ``repeat.index``,
#: ``trigger.from_state``, ``light.data`` — and none of them name an entity. A
#: measured scan of this installation returned 46 candidates for 1 real finding
#: with this left in, almost all of them from blueprint templates. Lines are
#: skipped whole, so a genuine reference sharing a line with a template is also
#: missed; that trade is deliberate, because a false negative is one manual check
#: and a false positive is a report nobody reads twice. Skipped lines are
#: counted in the result so the gap is visible rather than silent.
JINJA_MARKERS = ("{{", "{%")

#: A line containing a URL scheme is a citation or a link, not a reference.
#: ``https://github.com/foogar/ha-opencode-tools`` yields ``github.com``, and a
#: bare TLD denylist would not generalise past the first one guessed wrong.
URL_MARKER = "://"

#: Directories holding library or data files rather than live configuration.
#: ``scanner`` deliberately walks a wider set — a blueprint naming an entity is
#: still worth reporting before a rename — but a blueprint's Jinja inputs and
#: this installation's decision notes are not references to anything, and
#: including them buries the real findings.
EXCLUDED_DIRECTORIES_FOR_DANGLING = EXCLUDED_DIRECTORIES | {"blueprints", "opencode"}


def _is_excluded_directory(name: str) -> bool:
    return name.startswith(".") or name in EXCLUDED_DIRECTORIES_FOR_DANGLING


def _is_scannable_file(name: str) -> bool:
    if name in EXCLUDED_FILES or name.startswith("."):
        return False
    return Path(name).suffix.lower() in SCANNED_EXTENSIONS


def _candidates_in(text: str) -> tuple[dict[str, list[int]], int, int]:
    """Map each entity-ID-shaped token in `text` to its 1-based line numbers.

    Returns the map plus counts of lines skipped as templates and as URLs. A
    leading ``-`` or ``.`` is excluded by the lookbehind so that
    ``my-home.yaml`` yields ``home.yaml``'s fragment nowhere: without it every
    hyphenated filename produces a candidate.
    """
    found: dict[str, list[int]] = {}
    templated = 0
    urls = 0
    for index, line in enumerate(text.splitlines(), start=1):
        if any(marker in line for marker in JINJA_MARKERS):
            templated += 1
            continue
        if URL_MARKER in line:
            urls += 1
            continue
        for domain, object_id in CANDIDATE_PATTERN.findall(line):
            if object_id in FILE_LIKE_SUFFIXES:
                continue
            entity_id = f"{domain}.{object_id}"
            if not is_valid_entity_id(entity_id):
                continue
            found.setdefault(entity_id, []).append(index)
    return found, templated, urls


def _unscanned(reason: str) -> dict[str, object]:
    return {
        "scanned": False,
        "reason": reason,
        "files_scanned": 0,
        "candidates_checked": 0,
        "dangling_count": 0,
        "dangling": [],
        "truncated": False,
    }


def find_dangling_references(
    config_dir: str,
    existing: frozenset[str],
    service_names: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Report configuration references to entity IDs that are not in `existing`.

    `existing` must be the live entity IDs from the state machine, not the entity
    registry: entities declared in YAML without a ``unique_id`` are absent from
    the registry and would be reported missing here. `service_names` removes
    ``domain.service`` calls from the results.

    Never raises. ``scanned`` says whether the walk ran at all, so a caller can
    tell "no broken references" from "we could not look".
    """
    root = Path(config_dir)
    if not root.is_dir():
        return _unscanned("configuration directory is not readable")

    locations: dict[str, list[dict[str, object]]] = {}
    occurrences: dict[str, int] = {}
    candidates_checked = 0
    templated_lines_skipped = 0
    url_lines_skipped = 0
    files_scanned = 0
    truncated = False

    def record(entity_id: str, path: str, line: int) -> None:
        # The true occurrence count is kept separately from the capped list, so a
        # reader who sees 10 locations is told there were more rather than being
        # left to assume those were all of them.
        occurrences[entity_id] = occurrences.get(entity_id, 0) + 1
        entries = locations.setdefault(entity_id, [])
        if len(entries) < MAX_LOCATIONS_PER_ENTITY:
            entries.append({"path": path, "line": line})

    def visit(directory: Path, depth: int) -> None:
        nonlocal candidates_checked, files_scanned, templated_lines_skipped
        nonlocal url_lines_skipped, truncated
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
                # follow_symlinks=False throughout, matching scanner: a symlink
                # must not pull a file in from outside the configuration directory.
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

            relative = os.path.relpath(entry.path, root)
            found, templated, urls = _candidates_in(text)
            templated_lines_skipped += templated
            url_lines_skipped += urls
            for entity_id, lines in found.items():
                candidates_checked += 1
                if entity_id in existing or entity_id in service_names:
                    continue
                for line in lines:
                    record(entity_id, relative, line)

    try:
        visit(root, 0)
    except OSError:
        # A walk failure must not fail the report; return what was collected.
        pass

    ordered = sorted(locations)
    reported = ordered[:MAX_REPORTED_ENTITIES]
    if len(ordered) > MAX_REPORTED_ENTITIES:
        truncated = True

    return {
        "scanned": True,
        "files_scanned": files_scanned,
        "candidates_checked": candidates_checked,
        "templated_lines_skipped": templated_lines_skipped,
        "url_lines_skipped": url_lines_skipped,
        "dangling_count": len(ordered),
        "dangling": [
            {
                "entity_id": entity_id,
                "occurrence_count": occurrences[entity_id],
                "locations": locations[entity_id],
            }
            for entity_id in reported
        ],
        "truncated": truncated,
    }
