"""Contract tests for the Home Assistant facing layer.

`llm.py` cannot be imported without the `homeassistant` package, which is not
available outside Core, and `__init__.py` deliberately avoids importing it at
runtime. That leaves the most failure-prone code — signatures, decorators,
manifest keys — with no test at all, which is how `async_setup(hass)` shipped and
broke component setup at startup.

These tests close that gap using `inspect` and `ast`, so they run anywhere.
"""

from __future__ import annotations

import ast
import inspect
import json
import unittest
from pathlib import Path

import ha_dev_tools

COMPONENT_DIR = Path(ha_dev_tools.__file__).parent


class TestAsyncSetupSignature(unittest.TestCase):
    def test_async_setup_takes_hass_and_config(self) -> None:
        # Home Assistant calls a YAML-configured component as async_setup(hass,
        # config). A one-argument signature raises TypeError during setup and the
        # integration silently never loads.
        signature = inspect.signature(ha_dev_tools.async_setup)
        params = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        self.assertEqual(
            [parameter.name for parameter in params],
            ["hass", "config"],
            "async_setup must accept exactly (hass, config)",
        )

    def test_async_setup_is_a_coroutine_function(self) -> None:
        self.assertTrue(inspect.iscoroutinefunction(ha_dev_tools.async_setup))

    def test_async_setup_takes_no_required_defaults_away(self) -> None:
        for parameter in inspect.signature(ha_dev_tools.async_setup).parameters.values():
            self.assertIs(
                parameter.default,
                inspect.Parameter.empty,
                f"{parameter.name} must not have a default; Home Assistant passes it",
            )


class TestManifest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text(encoding="utf-8"))

    def test_required_keys_are_present(self) -> None:
        for key in ("domain", "name", "codeowners", "documentation", "version"):
            self.assertIn(key, self.manifest, f"manifest.json is missing {key}")

    def test_domain_matches_the_directory_name(self) -> None:
        # A mismatch means Home Assistant refuses the component outright.
        self.assertEqual(self.manifest["domain"], COMPONENT_DIR.name)

    def test_codeowners_is_a_non_empty_list(self) -> None:
        self.assertIsInstance(self.manifest["codeowners"], list)
        self.assertTrue(self.manifest["codeowners"])

    def test_requirements_is_a_list(self) -> None:
        self.assertIsInstance(self.manifest.get("requirements", []), list)


class TestLlmModule(unittest.TestCase):
    """Static checks on llm.py, which cannot be imported without Home Assistant."""

    def setUp(self) -> None:
        self.source = (COMPONENT_DIR / "llm.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def _module_functions(self) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
        return {
            node.name: node
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_defines_async_get_tools(self) -> None:
        self.assertIn("async_get_tools", self._module_functions())

    def test_async_get_tools_is_decorated_with_callback(self) -> None:
        # Without @callback Home Assistant warns and may not run the platform.
        function = self._module_functions()["async_get_tools"]
        decorators = [ast.unparse(node) for node in function.decorator_list]
        self.assertIn("callback", decorators)

    def test_async_get_tools_returns_llm_tools_or_none(self) -> None:
        function = self._async_get_tools()
        self.assertEqual([argument.arg for argument in function.args.args], ["hass", "llm_context", "api_id"])
        self.assertIn("LLMTools", ast.unparse(function.returns))

    def _async_get_tools(self) -> ast.FunctionDef | ast.AsyncFunctionDef:
        return self._module_functions()["async_get_tools"]  # type: ignore[return-value]

    def test_tool_class_implements_the_required_attributes(self) -> None:
        classes = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
        }
        self.assertIn("FindEntityReferencesTool", classes)
        tool = classes["FindEntityReferencesTool"]

        assigned = {
            target.id
            for node in tool.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for attribute in ("name", "description", "parameters"):
            self.assertIn(attribute, assigned, f"Tool is missing {attribute}")

        methods = {
            node.name
            for node in tool.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn("async_call", methods, "Tool is missing async_call")
        self.assertIn("async_call", self._methods_with_override(tool), "async_call must use @override")

    def _methods_with(self, cls: ast.ClassDef, attribute: str) -> list:
        return [
            node
            for node in cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(child, ast.Attribute) and child.attr == attribute
                for child in node.body
                if isinstance(child, ast.Return)
            )
        ]

    def _methods_with_override(self, cls: ast.ClassDef) -> set[str]:
        return {
            node.name
            for node in cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and "override" in [ast.unparse(node) for node in node.decorator_list]
        }

    def test_reads_arguments_from_tool_input(self) -> None:
        # Reading tool_input directly, or from the wrong attribute, is the most
        # common way a hand-written tool silently receives nothing.
        self.assertIn("tool_input.tool_args", ast.unparse(self.tree))

    def test_does_not_import_the_scanner_by_relative_parent_path(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn("homeassistant.components", node.module)
                break
        self.assertIn("from .scanner import", self.source)

    def test_uses_the_documented_import_paths(self) -> None:
        for required in (
            "from homeassistant.components.llm import LLMTools",
            "from homeassistant.helpers.llm import",
            "from homeassistant.exceptions import HomeAssistantError",
        ):
            self.assertIn(required, self.source, f"missing documented import: {required}")

    def test_uses_a_schema_that_converts_cleanly(self) -> None:
        # The platform docs are explicit: a validator the schema converter cannot
        # introspect serialises to an empty member, and strict MCP clients then
        # refuse to compile the tool.
        self.assertIn("vol.All(cv.ensure_list, [str])", self.source)


if __name__ == "__main__":
    unittest.main()
