"""Tests for ha_dev_tools.scanner.

The scanner is stdlib-only and has no Home Assistant dependency, so it is fully
testable outside Core. `llm.py` cannot be tested here — it needs the Home
Assistant package — and is instead validated by Core actually loading it.

These tests assert the same guarantees as the JavaScript implementation in
lib/entity-references.js, because the two must not drift.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ha_dev_tools.scanner import (
    EXCLUDED_DIRECTORIES,
    EXCLUDED_FILES,
    MAX_REPORTED_FILES,
    find_entity_references,
    is_valid_entity_id,
)


class ScannerTestCase(unittest.TestCase):
    """Base class that builds a throwaway configuration tree."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def scan(self, entity_id: str = "lock.front_door") -> dict:
        return find_entity_references(entity_id, str(self.root))


class TestValidity(ScannerTestCase):
    def test_accepts_well_formed_ids(self) -> None:
        for entity_id in ("lock.front_door", "light.k", "sensor.a_b_c9"):
            self.assertTrue(is_valid_entity_id(entity_id), entity_id)

    def test_rejects_malformed_ids(self) -> None:
        for entity_id in ("Lock.Bad", "lock", "lock.", ".front_door", "", None, 7, "lock.a-b"):
            self.assertFalse(is_valid_entity_id(entity_id), repr(entity_id))

    def test_invalid_id_reports_not_scanned(self) -> None:
        result = self.scan("Lock.Bad")
        self.assertFalse(result["scanned"])
        self.assertIn("not a valid entity ID", result["reason"])


class TestMatching(ScannerTestCase):
    def test_reports_files_and_line_numbers(self) -> None:
        self.write("automations.yaml", "id: '1'\ntrigger:\n  - entity_id: lock.front_door\n")
        self.write("dashboards/workshop.yaml", "cards:\n  - entity: lock.front_door\n  - entity: lock.front_door\n")

        result = self.scan()

        self.assertTrue(result["scanned"])
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["total_matches"], 3)
        self.assertEqual(
            result["files"],
            [
                {"path": "automations.yaml", "count": 1, "lines": [3]},
                {"path": "dashboards/workshop.yaml", "count": 2, "lines": [2, 3]},
            ],
        )
        self.assertFalse(result["truncated"])

    def test_ignores_a_different_domain(self) -> None:
        # `sensor.front_door` occurs inside `binary_sensor.front_door`.
        self.write("sensors.yaml", "binary_sensor.front_door:\n  friendly_name: Front\n")
        result = self.scan("sensor.front_door")
        self.assertEqual(result["total_matches"], 0)

    def test_ignores_a_longer_object_id(self) -> None:
        # `lock.front_door` occurs inside `lock.front_door_extra`.
        self.write("locks.yaml", "lock.front_door_extra:\n  friendly_name: Extra\n")
        result = self.scan()
        self.assertEqual(result["total_matches"], 0)

    def test_escapes_regex_metacharacters_in_the_query(self) -> None:
        self.write("odd.yaml", "id: a.b+c(d)\n")
        result = self.scan("a.b+c(d)")
        self.assertEqual(result["total_matches"], 0)

    def test_counts_multiple_matches_on_one_line(self) -> None:
        self.write("dense.yaml", "entity_ids: [lock.front_door, lock.front_door]\n")
        result = self.scan()
        self.assertEqual(result["total_matches"], 2)
        self.assertEqual(result["files"][0]["count"], 2)

    def test_reports_no_references_as_scanned_and_empty(self) -> None:
        self.write("configuration.yaml", "homeassistant:\n  name: Home\n")
        result = self.scan()
        self.assertTrue(result["scanned"])
        self.assertEqual(result["total_matches"], 0)
        self.assertEqual(result["files"], [])


class TestExclusions(ScannerTestCase):
    def test_never_reads_secrets_or_excluded_directories(self) -> None:
        self.write("secrets.yaml", "lock: !secret lock_front_door\n")
        self.write("packages/locks.yaml", "entity_id: lock.front_door\n")
        for excluded in sorted(EXCLUDED_DIRECTORIES):
            self.write(f"{excluded}/inner.yaml", "entity_id: lock.front_door\n")

        result = self.scan()

        self.assertEqual([f["path"] for f in result["files"]], ["packages/locks.yaml"])

    def test_skips_files_that_are_not_configuration(self) -> None:
        self.write("notes.md", "lock.front_door\n")
        self.write("archive.yaml.bak", "entity_id: lock.front_door\n")
        self.write("scripts.yaml", "sequence:\n  - entity_id: lock.front_door\n")

        self.assertEqual([f["path"] for f in self.scan()["files"]], ["scripts.yaml"])

    def test_skips_symlinks_pointing_outside_the_tree(self) -> None:
        outside = Path(tempfile.mkdtemp())
        (outside / "external.yaml").write_text("entity_id: lock.front_door\n", encoding="utf-8")
        link = self.root / "linked.yaml"
        try:
            link.symlink_to(outside / "external.yaml")
        except OSError:
            self.skipTest("symlinks unavailable")
        self.write("real.yaml", "entity_id: lock.front_door\n")

        self.assertEqual([f["path"] for f in self.scan()["files"]], ["real.yaml"])

    def test_excluded_filenames_are_a_frozen_set(self) -> None:
        self.assertIn("secrets.yaml", EXCLUDED_FILES)


class TestCaps(ScannerTestCase):
    def test_caps_the_reported_file_list_and_flags_truncation(self) -> None:
        for index in range(MAX_REPORTED_FILES + 5):
            self.write(f"file{index:03d}.yaml", "entity_id: lock.front_door\n")

        result = self.scan()

        self.assertEqual(len(result["files"]), MAX_REPORTED_FILES)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["total_matches"], MAX_REPORTED_FILES + 5)

    def test_caps_reported_lines_without_undercounting(self) -> None:
        self.write("many.yaml", "\n".join(["  - entity_id: lock.front_door"] * 9))

        result = self.scan()

        self.assertEqual(len(result["files"][0]["lines"]), 5)
        self.assertEqual(result["total_matches"], 9)

    def test_respects_the_depth_limit(self) -> None:
        deep = "/".join(f"level{index}" for index in range(6))
        self.write(f"{deep}/deep.yaml", "entity_id: lock.front_door\n")
        self.write("shallow.yaml", "entity_id: lock.front_door\n")

        paths = [f["path"] for f in self.scan()["files"]]

        self.assertIn("shallow.yaml", paths)
        self.assertFalse(any(path.startswith("level0") for path in paths), paths)


class TestFailureModes(ScannerTestCase):
    def test_missing_directory_reports_not_scanned(self) -> None:
        result = find_entity_references("lock.front_door", "/definitely/not/here")
        self.assertFalse(result["scanned"])
        self.assertIn("not readable", result["reason"])

    def test_unscanned_shape_is_complete(self) -> None:
        result = find_entity_references("lock.front_door", "/definitely/not/here")
        for key in (
            "scanned",
            "reason",
            "files_scanned",
            "file_count",
            "total_matches",
            "files",
            "truncated",
        ):
            self.assertIn(key, result)

    def test_a_file_that_is_a_directory_named_like_yaml_is_not_read(self) -> None:
        (self.root / "trap.yaml").mkdir()
        self.write("real.yaml", "entity_id: lock.front_door\n")
        self.assertEqual([f["path"] for f in self.scan()["files"]], ["real.yaml"])

    def test_ordering_is_deterministic(self) -> None:
        for name in ("c.yaml", "a.yaml", "b.yaml"):
            self.write(name, "entity_id: lock.front_door\n")
        first = [f["path"] for f in self.scan()["files"]]
        second = [f["path"] for f in self.scan()["files"]]
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first))


if __name__ == "__main__":
    unittest.main()
