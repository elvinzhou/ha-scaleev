"""Shared entity bases."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_STATION_NAME, DOMAIN


class ScaleEVEntity(Entity):
    """An entity that does not depend on the API being reachable.

    The button and switch must not follow the coordinator: going unavailable
    and back on every failed poll shows up in Activity as a button press.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager, key: str) -> None:
        self.manager = manager
        entry = manager.entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_STATION_NAME) or "ScaleEV charger",
            manufacturer="ScaleEV",
        )


class ScaleEVCoordinatorEntity(CoordinatorEntity, ScaleEVEntity):
    """An entity showing data polled from ScaleEV."""

    def __init__(self, manager, key: str) -> None:
        CoordinatorEntity.__init__(self, manager.coordinator)
        ScaleEVEntity.__init__(self, manager, key)
