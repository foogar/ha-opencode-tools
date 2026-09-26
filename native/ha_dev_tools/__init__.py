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


async def async_setup(hass: HomeAssistant) -> bool:
    """Set up the integration.

    Tools are discovered through ``llm.py`` when the integration is loaded, so
    there is nothing to do here.
    """
    return True
