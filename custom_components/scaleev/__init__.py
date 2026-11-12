"""ScaleEV: start EV charging sessions when you plug in."""
from __future__ import annotations

import logging

from homeassistant.components.zone import in_zone
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    STATE_ON,
    Platform,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util.location import distance

from .api import ScaleEVClient
from .charging import ChargeManager
from .const import (
    CONF_API_BASE,
    CONF_CABLE_SENSOR,
    CONF_CHARGE_ZONE,
    CONF_EMAIL,
    CONF_LOCATION_LAT,
    CONF_LOCATION_LON,
    CONF_PASSWORD,
    CONF_RADIUS,
    DEFAULT_RADIUS,
    CONF_TENANT_SLUG,
    CONF_VEHICLE_TRACKER,
)
from .coordinator import ScaleEVCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]

type ScaleEVConfigEntry = ConfigEntry[ChargeManager]


def _vehicle_in_zone(hass: HomeAssistant, tracker_id: str, zone_id: str) -> bool:
    tracker = hass.states.get(tracker_id)
    zone = hass.states.get(zone_id)
    if tracker is None or zone is None:
        return False
    lat = tracker.attributes.get(ATTR_LATITUDE)
    lon = tracker.attributes.get(ATTR_LONGITUDE)
    if lat is not None and lon is not None:
        return in_zone(zone, lat, lon, tracker.attributes.get(ATTR_GPS_ACCURACY) or 0)
    # No GPS attributes (e.g. router-based trackers): compare zone names.
    name = zone.name.lower()
    return tracker.state.lower() == name or (zone_id == "zone.home" and tracker.state == "home")


def _vehicle_at_charger(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """True if the vehicle is in the chosen zone, or near the location's coordinates."""
    opts = entry.options
    tracker_id = opts[CONF_VEHICLE_TRACKER]
    if zone_id := opts.get(CONF_CHARGE_ZONE):
        return _vehicle_in_zone(hass, tracker_id, zone_id)
    tracker = hass.states.get(tracker_id)
    lat = entry.data.get(CONF_LOCATION_LAT)
    lon = entry.data.get(CONF_LOCATION_LON)
    if tracker is None or lat is None or lon is None:
        return False
    t_lat = tracker.attributes.get(ATTR_LATITUDE)
    t_lon = tracker.attributes.get(ATTR_LONGITUDE)
    if t_lat is None or t_lon is None:
        _LOGGER.debug("%s has no GPS position; choose a zone in the options instead", tracker_id)
        return False
    metres = distance(t_lat, t_lon, lat, lon)
    accuracy = tracker.attributes.get(ATTR_GPS_ACCURACY) or 0
    return metres is not None and metres - accuracy < opts.get(CONF_RADIUS, DEFAULT_RADIUS)


async def async_setup_entry(hass: HomeAssistant, entry: ScaleEVConfigEntry) -> bool:
    client = ScaleEVClient(
        async_get_clientsession(hass),
        entry.data[CONF_API_BASE],
        entry.data[CONF_TENANT_SLUG],
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )
    coordinator = ScaleEVCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    manager = ChargeManager(hass, entry, client, coordinator)
    entry.runtime_data = manager

    @callback
    def _cable_changed(event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or new.state != STATE_ON or old is None or old.state == STATE_ON:
            return
        if not manager.auto_start:
            return
        if not _vehicle_at_charger(hass, entry):
            _LOGGER.debug("Cable connected away from the charging location, ignoring")
            return
        manager.async_trigger(manual=False)

    entry.async_on_unload(
        async_track_state_change_event(hass, [entry.options[CONF_CABLE_SENSOR]], _cable_changed)
    )
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _reload(hass: HomeAssistant, entry: ScaleEVConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ScaleEVConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
