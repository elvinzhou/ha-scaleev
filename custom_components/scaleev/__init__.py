"""ScaleEV: start EV charging sessions when you plug in."""
from __future__ import annotations

import logging

from homeassistant.components.zone import in_zone
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    STATE_OFF,
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
    STATUS_AUTO_OFF,
    STATUS_AWAY,
    CONF_TENANT_SLUG,
    CONF_VEHICLE_TRACKER,
)
from .coordinator import ScaleEVCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]

type ScaleEVConfigEntry = ConfigEntry[ChargeManager]


def _why_not_at_charger(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """None if the vehicle is at the charger, else the reason it is not.

    The reason ends up in the Start status sensor, so it names the numbers
    that decided it: a skipped plug-in is otherwise impossible to debug.
    """
    opts = entry.options
    tracker_id = opts[CONF_VEHICLE_TRACKER]
    tracker = hass.states.get(tracker_id)
    if tracker is None:
        return f"{tracker_id} does not exist"
    t_lat = tracker.attributes.get(ATTR_LATITUDE)
    t_lon = tracker.attributes.get(ATTR_LONGITUDE)
    has_gps = t_lat is not None and t_lon is not None
    accuracy = tracker.attributes.get(ATTR_GPS_ACCURACY) or 0

    if zone_id := opts.get(CONF_CHARGE_ZONE):
        zone = hass.states.get(zone_id)
        if zone is None:
            return f"{zone_id} does not exist"
        if has_gps:
            if in_zone(zone, t_lat, t_lon, accuracy):
                return None
            return f"{tracker_id} is at {t_lat}, {t_lon}, outside {zone_id}"
        # No GPS attributes (e.g. router-based trackers): compare zone names.
        if tracker.state.lower() == zone.name.lower() or (zone_id == "zone.home" and tracker.state == "home"):
            return None
        return f"{tracker_id} has no GPS position and its state is '{tracker.state}', not {zone_id}"

    lat = entry.data.get(CONF_LOCATION_LAT)
    lon = entry.data.get(CONF_LOCATION_LON)
    if lat is None or lon is None:
        return "ScaleEV has no coordinates for this location; choose a zone in the settings"
    if not has_gps:
        return (
            f"{tracker_id} has no GPS position (state '{tracker.state}'); "
            "choose a zone in the settings or pick a tracker with latitude and longitude"
        )
    metres = distance(t_lat, t_lon, lat, lon)
    radius = opts.get(CONF_RADIUS, DEFAULT_RADIUS)
    if metres is not None and metres - accuracy < radius:
        return None
    return (
        f"{tracker_id} is at {t_lat}, {t_lon}, {round(metres or 0)} m from the charging location "
        f"ScaleEV reports at {lat}, {lon} (radius {round(radius)} m); "
        "raise the radius or choose a zone in the settings"
    )


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

    cable_id = entry.options[CONF_CABLE_SENSOR]
    # The last real (on/off) cable state. Vehicle integrations drop to
    # unavailable/unknown whenever the car sleeps or the cloud hiccups; only
    # a genuine off -> on counts as plugging in, however many gaps in between.
    initial = hass.states.get(cable_id)
    cable_was: str | None = initial.state if initial and initial.state in (STATE_ON, STATE_OFF) else None

    @callback
    def _cable_changed(event: Event[EventStateChangedData]) -> None:
        nonlocal cable_was
        new = event.data["new_state"]
        if new is None or new.state not in (STATE_ON, STATE_OFF):
            return
        previous, cable_was = cable_was, new.state
        if new.state != STATE_ON or previous != STATE_OFF:
            return
        if not manager.auto_start:
            manager.async_set_status(STATUS_AUTO_OFF)
            return
        if reason := _why_not_at_charger(hass, entry):
            manager.async_set_status(STATUS_AWAY, reason)
            return
        if not manager.async_trigger(manual=False):
            _LOGGER.info("Cable connected while a ScaleEV start is already in progress")

    entry.async_on_unload(
        async_track_state_change_event(hass, [cable_id], _cable_changed)
    )
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _reload(hass: HomeAssistant, entry: ScaleEVConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ScaleEVConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
