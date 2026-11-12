"""Auto-start switch."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import ScaleEVEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AutoStartSwitch(entry.runtime_data, "auto_start")])


class AutoStartSwitch(ScaleEVEntity, SwitchEntity, RestoreEntity):
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_OFF:
            self.manager.auto_start = False

    @property
    def is_on(self) -> bool:
        return self.manager.auto_start

    async def async_turn_on(self, **kwargs) -> None:
        self.manager.auto_start = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.manager.auto_start = False
        self.async_write_ha_state()
