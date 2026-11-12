"""Shared entity base."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_STATION_NAME, DOMAIN


class ScaleEVEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, manager, key: str) -> None:
        super().__init__(manager.coordinator)
        self.manager = manager
        entry = manager.entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_STATION_NAME) or "ScaleEV charger",
            manufacturer="ScaleEV",
        )
