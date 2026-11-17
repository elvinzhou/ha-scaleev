"""ScaleEV: start EV charging sessions when you plug in."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

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
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util
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
    CONNECTOR_FREE,
    CONNECTOR_WAITING,
    DEFAULT_RADIUS,
    DOMAIN,
    LOCATION_WAIT_MINUTES,
    STATUS_AUTO_OFF,
    STATUS_AWAY,
    STATUS_IDLE,
    STATUS_LOCATING,
    CONF_TENANT_SLUG,
    CONF_VEHICLE_TRACKER,
    CONF_WAKE_BUTTON,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    STATUS_SCHEDULED,
)
from .coordinator import ScaleEVCoordinator
from .http import StartLinkView

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
        return f"{tracker_id} does not exist; pick the vehicle tracker again in the settings"
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


def _charging_window(entry: ConfigEntry) -> tuple[time, time] | None:
    """The hours automatic starts are allowed in, or None for any time."""
    start = dt_util.parse_time(entry.options.get(CONF_WINDOW_START) or "")
    end = dt_util.parse_time(entry.options.get(CONF_WINDOW_END) or "")
    return (start, end) if start and end and start != end else None


def _in_window(now: time, window: tuple[time, time]) -> bool:
    start, end = window
    if start < end:
        return start <= now < end
    return now >= start or now < end  # the window spans midnight


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
    if not hass.data.get(DOMAIN):
        hass.http.register_view(StartLinkView())
        hass.data[DOMAIN] = True

    cable_id = entry.options[CONF_CABLE_SENSOR]
    tracker_id = entry.options[CONF_VEHICLE_TRACKER]
    # The last real (on/off) cable state. Vehicle integrations drop to
    # unavailable/unknown whenever the car sleeps or the cloud hiccups.
    initial = hass.states.get(cable_id)
    cable_was: str | None = initial.state if initial and initial.state in (STATE_ON, STATE_OFF) else None
    # One automatic attempt per plug-in. Cleared when the cable is seen off or
    # the charger reports the connector free again.
    attempted = False
    # A plug-in seen outside the charging hours, waiting for them to begin.
    waiting = False
    window = _charging_window(entry)
    connector_was: str | None = None
    # When the charger was last seen reporting a plug-in (Available -> Preparing).
    connector_plugged_at: datetime | None = None
    # Re-check once the wait for a fresh vehicle location runs out.
    location_deadline: CALLBACK_TYPE | None = None
    # A plug-in seen while the tracker's position predates it; decided once it reports.
    locating = False
    wake_id: str | None = entry.options.get(CONF_WAKE_BUTTON) or None
    location_wait = timedelta(minutes=LOCATION_WAIT_MINUTES)

    def _connector() -> str:
        return str((coordinator.data or {}).get("connector_status") or "").lower()

    @callback
    def _evaluate(plugged_in_now: bool = False) -> None:
        """Start if the car is plugged in at the charger and we haven't tried yet.

        A plug-in is either the cable sensor going off -> on, or the cable
        sensor being on while the charger itself reports a car connected and
        waiting ("Preparing"). The second catches plug-ins the car's slowly
        polled sensor never showed as a change, e.g. arriving straight from
        another charger with the sensor still on.
        """
        nonlocal attempted, waiting, connector_plugged_at, location_deadline, locating
        if hass.state is not CoreState.running:
            # Home Assistant is still starting: the vehicle's integration may not
            # have created its entities yet, so the car's position can't be judged.
            # async_at_started below runs this again once everything is loaded.
            return
        connector = _connector()
        if cable_was == STATE_OFF or connector == CONNECTOR_FREE:
            attempted = False
            if connector == CONNECTOR_FREE:
                connector_plugged_at = None
            if waiting:
                waiting = False
                manager.async_set_status(STATUS_IDLE)
        if cable_was == STATE_OFF and locating:
            # Unplugged (possibly at another charger) before the location arrived.
            locating = False
            manager.async_set_status(STATUS_IDLE)
        if cable_was != STATE_ON or attempted or manager.running:
            return
        if not (plugged_in_now or waiting or locating or connector == CONNECTOR_WAITING):
            return
        if not manager.auto_start:
            manager.async_set_status(STATUS_AUTO_OFF)
            return
        if reason := _why_not_at_charger(hass, entry):
            # Vehicle integrations poll: the tracker may still hold a position from
            # before the car arrived (Tesla Fleet polls every 10 minutes and not at
            # all while the car sleeps). Even within one poll, the cable sensor can
            # update a moment before the tracker. Only call the car away once the
            # tracker has reported since the plug-in, or the wait has run out.
            plugged_at = _plugged_at()
            tracker = hass.states.get(tracker_id)
            reported = tracker.last_reported if tracker else None
            now = dt_util.utcnow()
            if plugged_at and (reported is None or reported < plugged_at) and now < plugged_at + location_wait:
                last = f"last reported at {dt_util.as_local(reported).strftime('%H:%M')}" if reported else "has not reported"
                manager.async_set_status(
                    STATUS_LOCATING,
                    f"{tracker_id} {last}, before the plug-in at {dt_util.as_local(plugged_at).strftime('%H:%M')}; "
                    f"waiting up to {LOCATION_WAIT_MINUTES} minutes for it to update",
                )
                locating = True
                if location_deadline is None:
                    location_deadline = async_call_later(
                        hass, (plugged_at + location_wait - now).total_seconds() + 1, _deadline
                    )
                return
            locating = False
            manager.async_set_status(STATUS_AWAY, reason)
            return
        locating = False
        if window and not _in_window(dt_util.now().time(), window):
            waiting = True
            manager.async_set_status(STATUS_SCHEDULED, f"Starts at {window[0].strftime('%H:%M')}")
            return
        waiting = False
        attempted = True
        manager.async_trigger(manual=False)

    def _plugged_at() -> datetime | None:
        """When the latest evidence of a plug-in appeared."""
        cable = hass.states.get(cable_id)
        times = [t for t in (cable.last_changed if cable and cable.state == STATE_ON else None, connector_plugged_at) if t]
        return max(times) if times else None

    @callback
    def _deadline(_now) -> None:
        nonlocal location_deadline
        location_deadline = None
        _evaluate()

    @callback
    def _cable_changed(event: Event[EventStateChangedData]) -> None:
        nonlocal cable_was
        new = event.data["new_state"]
        if new is None or new.state not in (STATE_ON, STATE_OFF):
            return
        previous, cable_was = cable_was, new.state
        if new.state == STATE_OFF:
            _evaluate()  # clears the one-attempt latch
        _evaluate(plugged_in_now=new.state == STATE_ON and previous == STATE_OFF)

    @callback
    def _tracker_changed(event: Event) -> None:
        # Both changes and unchanged re-reports: a car parked in the same spot
        # re-reports the same position, which still makes it up to date.
        _evaluate()

    async def _refresh_vehicle() -> None:
        """Ask the car's integration for fresh data instead of waiting for its next poll."""
        if wake_id:
            # Polling integrations such as Tesla Fleet skip a sleeping car, so a
            # refresh alone returns the old data; wake it first.
            try:
                await hass.services.async_call("button", "press", {"entity_id": wake_id}, blocking=True)
            except Exception as err:  # noqa: BLE001 - best effort
                _LOGGER.warning("Could not wake the vehicle with %s: %s", wake_id, err)
        try:
            await hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": [cable_id, tracker_id]}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 - best effort
            _LOGGER.debug("Could not refresh %s and %s: %s", cable_id, tracker_id, err)

    @callback
    def _polled() -> None:
        nonlocal connector_was, connector_plugged_at
        connector = _connector()
        previous, connector_was = connector_was, connector
        if connector == CONNECTOR_WAITING and previous not in (None, CONNECTOR_WAITING):
            connector_plugged_at = dt_util.utcnow()
        if (
            connector == CONNECTOR_WAITING
            and previous not in (None, CONNECTOR_WAITING)
            and manager.auto_start
            and not attempted
            and hass.services.has_service("homeassistant", "update_entity")
        ):
            # Someone just plugged in at our connector; find out now whether it was our car.
            entry.async_create_background_task(hass, _refresh_vehicle(), name="scaleev refresh vehicle")
        _evaluate()

    @callback
    def _started(_hass: HomeAssistant) -> None:
        _evaluate()

    entry.async_on_unload(async_at_started(hass, _started))
    connector_was = _connector()
    entry.async_on_unload(async_track_state_change_event(hass, [cable_id], _cable_changed))
    entry.async_on_unload(async_track_state_change_event(hass, [tracker_id], _tracker_changed))
    entry.async_on_unload(async_track_state_report_event(hass, [tracker_id], _tracker_changed))

    @callback
    def _cancel_deadline() -> None:
        if location_deadline:
            location_deadline()

    entry.async_on_unload(_cancel_deadline)
    entry.async_on_unload(coordinator.async_add_listener(_polled))
    if window:

        @callback
        def _window_opened(_now) -> None:
            _evaluate()

        entry.async_on_unload(
            async_track_time_change(hass, _window_opened, hour=window[0].hour, minute=window[0].minute, second=0)
        )
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _reload(hass: HomeAssistant, entry: ScaleEVConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ScaleEVConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
