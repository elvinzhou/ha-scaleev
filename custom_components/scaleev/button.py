"""Start-charging button."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entity import ScaleEVEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([StartButton(entry.runtime_data, "start_charging")])


class StartButton(ScaleEVEntity, ButtonEntity):
    async def async_press(self) -> None:
        self.manager.async_trigger(manual=True)
