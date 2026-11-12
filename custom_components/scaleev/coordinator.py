"""Polls the current session and connector status."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ScaleEVAuthError, ScaleEVClient, ScaleEVError
from .const import CONF_CONNECTOR_ID, CONF_STATION_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)
IDLE_INTERVAL = timedelta(minutes=2)
ACTIVE_INTERVAL = timedelta(seconds=30)


class ScaleEVCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Data: {"session": live session or None, "connector_status": str or None}."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ScaleEVClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=IDLE_INTERVAL)
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        entry = self.config_entry
        try:
            session = await self.client.async_live()
            station = await self.client.async_station(entry.data[CONF_STATION_ID])
        except ScaleEVAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ScaleEVError as err:
            raise UpdateFailed(str(err)) from err
        status = None
        for connector in (station or {}).get("connectors", []):
            if connector.get("connectorId") == entry.data[CONF_CONNECTOR_ID]:
                status = connector.get("status")
        self.update_interval = ACTIVE_INTERVAL if session else IDLE_INTERVAL
        return {"session": session, "connector_status": status}
