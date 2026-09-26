"""Tests for the pure dangling-reference logic.

The fixture that matters most is :meth:`TestRegistryVersusStates.test_yaml_entity_absent_from_registry_is_not_dangling`.
It reproduces a real configuration on this installation: an MQTT sensor declared
in ``configuration.yaml`` with no ``unique_id``. It exists in the state machine
and is absent from the entity registry entirely. A checker that validated
against the registry reported it as missing, and acting on that report would
have deleted a working automation and a working dashboard tile. If that test
ever fails, the tool is dangerous rather than merely wrong.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ha_dev_tools.references import (
    MAX_LOCATIONS_PER_ENTITY,
    MAX_REPORTED_ENTITIES,
    find_dangling_references,
)

NO_SERVICES = frozenset()


class ScanCase(unittest.TestCase):
    """Builds a throwaway configuration directory per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def scan(self, existing: frozenset[str], services: frozenset[str] = NO_SERVICES) -> dict:
        return find_dangling_references(str(self.root), existing, services)

    def dangling_ids(self, report: dict) -> list[str]:
        return [entry["entity_id"] for entry in report["dangling"]]


class TestReportsMissingEntities(ScanCase):
    def test_reports_a_reference_to_an_entity_that_does_not_exist(self) -> None:
        self.write("automations.yaml", "- entity_id: sensor.does_not_exist\n")
        report = self.scan(frozenset({"sensor.does_exist"}))
        self.assertTrue(report["scanned"])
        self.assertEqual(self.dangling_ids(report), ["sensor.does_not_exist"])

    def test_does_not_report_an_entity_that_exists(self) -> None:
        self.write("automations.yaml", "- entity_id: sensor.does_exist\n")
        self.assertEqual(self.scan(frozenset({"sensor.does_exist"}))["dangling_count"], 0)

    def test_reports_line_numbers(self) -> None:
        self.write("automations.yaml", "first line\n- entity_id: sensor.gone\nlast line\n")
        entry = self.scan(frozenset())["dangling"][0]
        self.assertEqual(entry["locations"][0]["line"], 2)

    def test_reports_the_file_path_relative_to_the_config_dir(self) -> None:
        self.write("dashboards/workshop.yaml", "entity: sensor.gone\n")
        entry = self.scan(frozenset())["dangling"][0]
        self.assertEqual(entry["locations"][0]["path"], "dashboards/workshop.yaml")

    def test_groups_repeated_references_under_one_entity(self) -> None:
        self.write("automations.yaml", "- sensor.gone\n- sensor.gone\n")
        self.write("scripts.yaml", "- sensor.gone\n")
        report = self.scan(frozenset())
        self.assertEqual(report["dangling_count"], 1)
        self.assertEqual(report["dangling"][0]["occurrence_count"], 3)

    def test_caps_locations_per_entity(self) -> None:
        body = "".join(f"- sensor.gone\n" for _ in range(MAX_LOCATIONS_PER_ENTITY + 5))
        self.write("automations.yaml", body)
        entry = self.scan(frozenset())["dangling"][0]
        self.assertEqual(len(entry["locations"]), MAX_LOCATIONS_PER_ENTITY)
        # The true count is still reported, so truncation is visible not hidden.
        self.assertEqual(entry["occurrence_count"], MAX_LOCATIONS_PER_ENTITY + 5)


class TestRegistryVersusStates(ScanCase):
    def test_yaml_entity_absent_from_registry_is_not_dangling(self) -> None:
        # sensor.proxmox_battery_level is declared under `mqtt: sensor:` with no
        # unique_id. It is in hass.states at 77% and in no registry at all.
        # `existing` here stands in for hass.states, which is what must be passed.
        self.write("configuration.yaml", '    - name: "Proxmox Battery Level"\n')
        self.write("automations.yaml", "- entity_id: sensor.proxmox_battery_level\n")
        self.write("dashboards/workshop.yaml", "entity: sensor.proxmox_battery_level\n")
        report = self.scan(frozenset({"sensor.proxmox_battery_level"}))
        self.assertEqual(
            report["dangling_count"],
            0,
            "an entity that exists in states must not be reported missing",
        )

    def test_still_reports_a_genuinely_missing_sibling(self) -> None:
        # The real johnsphone typo alongside the working proxmox sensor.
        self.write("automations.yaml", "- entity_id: sensor.proxmox_battery_level\n")
        self.write("scripts.yaml", "- entity_id: sensor.johnsphone_battery_state\n")
        report = self.scan(frozenset({"sensor.proxmox_battery_level"}))
        self.assertEqual(self.dangling_ids(report), ["sensor.johnsphone_battery_state"])


class TestServiceNamesAreNotEntities(ScanCase):
    def test_service_calls_are_not_reported(self) -> None:
        self.write(
            "automations.yaml",
            "- action: light.turn_on\n- action: switch.toggle\n"
            "- action: notify.mobile_app_johns_phone\n",
        )
        services = frozenset(
            {"light.turn_on", "switch.toggle", "notify.mobile_app_johns_phone"}
        )
        self.assertEqual(self.scan(frozenset(), services)["dangling_count"], 0)

    def test_a_service_call_is_reported_when_services_are_not_supplied(self) -> None:
        # Guards the failure mode being defended against: without service names
        # every service call in the configuration reads as a broken reference.
        self.write("automations.yaml", "- action: light.turn_on\n")
        self.assertEqual(self.scan(frozenset())["dangling_count"], 1)

    def test_an_entity_sharing_a_service_name_is_still_reported(self) -> None:
        # light.turn_on being a service does not make sensor.turn_on a service.
        self.write("automations.yaml", "- entity_id: sensor.turn_on\n")
        report = self.scan(frozenset(), frozenset({"light.turn_on"}))
        self.assertEqual(self.dangling_ids(report), ["sensor.turn_on"])


class TestNonEntityTokens(ScanCase):
    def test_jinja_template_variables_are_not_candidates(self) -> None:
        # A measured scan returned 46 candidates for 1 real finding before this:
        # repeat.index, trigger.from_state, night_lights.entity_id and
        # light.data are template variables, not entities.
        self.write(
            "automations.yaml",
            "      repeat:\n"
            "        value_template: \"{{ repeat.index }}\"\n"
            "      brightness: \"{{ trigger.from_state.attributes.brightness }}\"\n"
            "      lamps: \"{{ night_lights.entity_id }}\"\n",
        )
        self.assertEqual(self.scan(frozenset())["dangling_count"], 0)

    def test_skipped_template_lines_are_counted(self) -> None:
        self.write("automations.yaml", "a: \"{{ x.y }}\"\nb: \"{{ z.w }}\"\n")
        report = self.scan(frozenset())
        self.assertEqual(report["templated_lines_skipped"], 2)

    def test_urls_are_not_candidates(self) -> None:
        # # Source of truth: https://github.com/foogar/ha-opencode-tools
        self.write(
            "configuration.yaml",
            "# Source of truth: https://github.com/foogar/ha-opencode-tools\n",
        )
        self.assertEqual(self.scan(frozenset())["dangling_count"], 0)

    def test_skipped_url_lines_are_counted(self) -> None:
        self.write("configuration.yaml", "# see https://example.com/docs\n")
        self.assertEqual(self.scan(frozenset())["url_lines_skipped"], 1)

    def test_blueprint_libraries_are_not_scanned(self) -> None:
        # Blueprint inputs are Jinja templates and default values, not live config.
        self.write(
            "blueprints/automation/vendor/thing.yaml",
            "  lamps:\n    entity_id: sensor.not_live_config\n",
        )
        report = self.scan(frozenset())
        self.assertEqual(report["dangling_count"], 0)
        self.assertNotIn("blueprints", [p for e in report["dangling"] for p in [l["path"] for l in e["locations"]]])

    def test_decision_notes_are_not_scanned(self) -> None:
        self.write("opencode/decisions.yaml", "  decision: see switch.renamed_example\n")
        self.assertEqual(self.scan(frozenset())["dangling_count"], 0)

    def test_hyphenated_filenames_do_not_produce_candidates(self) -> None:
        self.write("scripts.yaml", "# see my-home.yaml for the old layout\n")
        self.assertEqual(self.scan(frozenset())["dangling_count"], 0)

    def test_dotted_filenames_do_not_produce_candidates(self) -> None:
        self.write("scripts.yaml", "path: ./workshop.yaml\n")
        self.assertEqual(self.scan(frozenset())["dangling_count"], 0)

    def test_version_numbers_do_not_produce_candidates(self) -> None:
        self.write("scripts.yaml", "# tested on 2026.9.3 and v1.2.3\n")
        self.assertEqual(self.scan(frozenset())["dangling_count"], 0)

    def test_ipv4_addresses_do_not_produce_candidates(self) -> None:
        self.write("scripts.yaml", "host: 192.168.1.100\n")
        self.assertEqual(self.scan(frozenset())["dangling_count"], 0)

    def test_device_ids_do_not_produce_candidates(self) -> None:
        self.write("automations.yaml", "device_id: 881cabe3a489593a15c8c36d2cc936e3\n")
        self.assertEqual(self.scan(frozenset())["dangling_count"], 0)


class TestExclusions(ScanCase):
    def test_secrets_yaml_is_never_read(self) -> None:
        # A distinctive ID that exists nowhere. If the walk reads secrets.yaml it
        # becomes a candidate and shows up here; it never should.
        self.write("secrets.yaml", "- entity_id: sensor.secret_thing\n")
        report = self.scan(frozenset())
        self.assertNotIn("sensor.secret_thing", self.dangling_ids(report))
        self.assertEqual(report["dangling_count"], 0)

    def test_excluded_directories_are_not_scanned(self) -> None:
        for directory in (".storage", "custom_components", "node_modules", "deps"):
            self.write(f"{directory}/config.yaml", "- entity_id: sensor.hidden_thing\n")
        report = self.scan(frozenset())
        self.assertEqual(report["dangling_count"], 0)

    def test_dash_prefixed_directories_are_not_scanned(self) -> None:
        self.write(".hidden/config.yaml", "- entity_id: sensor.hidden_thing\n")
        self.assertEqual(self.scan(frozenset())["dangling_count"], 0)

    def test_non_configuration_extensions_are_not_scanned(self) -> None:
        self.write("notes.txt", "sensor.not_configuration\n")
        self.write("backup.yaml.bak", "entity: sensor.from_backup\n")
        self.assertEqual(self.scan(frozenset())["dangling_count"], 0)

    def test_nested_configuration_is_scanned(self) -> None:
        self.write("packages/lights/porch.yaml", "- entity_id: sensor.nested_gone\n")
        self.assertEqual(self.scan(frozenset())["dangling_count"], 1)


class TestFailsSoft(ScanCase):
    def test_unreadable_config_dir_reports_not_scanned(self) -> None:
        report = find_dangling_references("/nonexistent/path/xyz", frozenset())
        self.assertFalse(report["scanned"])
        self.assertEqual(report["dangling_count"], 0)
        self.assertIn("reason", report)

    def test_empty_config_dir_is_a_clean_scan(self) -> None:
        report = self.scan(frozenset())
        self.assertTrue(report["scanned"])
        self.assertEqual(report["files_scanned"], 0)
        self.assertEqual(report["dangling_count"], 0)

    def test_unreadable_file_does_not_abort_the_scan(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("running as root: mode bits do not prevent reading")
        self.write("automations.yaml", "- entity_id: sensor.gone\n")
        locked = self.write("locked.yaml", "- entity_id: sensor.also_gone\n")
        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o644)
        report = self.scan(frozenset())
        self.assertTrue(report["scanned"])
        self.assertIn("sensor.gone", self.dangling_ids(report))


class TestCaps(ScanCase):
    def test_reports_are_capped_and_flagged_as_truncated(self) -> None:
        count = MAX_REPORTED_ENTITIES + 10
        body = "".join(f"- entity_id: sensor.missing_{index}\n" for index in range(count))
        self.write("automations.yaml", body)
        report = self.scan(frozenset())
        self.assertTrue(report["truncated"])
        self.assertEqual(len(report["dangling"]), MAX_REPORTED_ENTITIES)
        self.assertEqual(report["dangling_count"], count)

    def test_not_truncated_when_everything_fits(self) -> None:
        self.write("automations.yaml", "- entity_id: sensor.gone\n")
        self.assertFalse(self.scan(frozenset())["truncated"])

    def test_candidates_checked_counts_references_not_occurrences(self) -> None:
        self.write("automations.yaml", "- sensor.gone\n- sensor.gone\n- sensor.present\n")
        report = self.scan(frozenset({"sensor.present"}))
        self.assertEqual(report["candidates_checked"], 2)


if __name__ == "__main__":
    unittest.main()
