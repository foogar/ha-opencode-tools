"""Developer tools contributed to Home Assistant's native LLM platform.

Currently read-only. The integration exists to serve tools over
``/api/mcp/<API ID>``; it holds no state and registers no services.

Home Assistant is imported only under ``TYPE_CHECKING`` so that
``ha_dev_tools.scanner`` stays importable — and therefore testable — on its own.
``scanner`` is deliberately stdlib-only, and a runtime import here would defeat
that and make the pure logic untestable without a full Home Assistant install.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration from configuration.yaml.

    The two-argument signature is required: Home Assistant calls a
    YAML-configured component as ``async_setup(hass, config)``. The one-argument
    form belongs to config-entry setup, and using it here fails setup outright.

    Tools are discovered through ``llm.py`` when the integration is loaded, so
    there is nothing to do.
    """
    return True
