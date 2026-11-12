from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.scaleev.const import DOMAIN

API = "https://api.example.test"
STATION = "station-1"
LOGIN = {"success": True, "data": {"access_token": "tok", "driver": {"id": "drv", "firstName": "Pat"}}}
STATIONS = [
    {"id": STATION, "name": "CDN31", "type": "AC", "maxPower": 6.7, "status": "Preparing",
     "connectors": [{"connectorId": 1, "type": "J1772", "maxPower": 6.7, "status": "Preparing"}]},
    {"id": "station-2", "name": "CDN08", "type": "AC", "maxPower": 6.7, "status": "Available",
     "connectors": [{"connectorId": 1, "type": "J1772", "maxPower": 6.7, "status": "Available"}]},
]
LOCATIONS = [{"id": "loc-1", "name": "Cadence", "address": "28850 Dixon St"}]
LIVE_AUTH = {"id": "sess-1", "status": "in-progress", "paymentStatus": "authorized", "energyDeliveredKwh": 1.5,
             "totalCost": 0.9, "currency": "USD"}


def mock_api(mock, *, start=None, live_session=None, live_current=None):
    """Register the ScaleEV API on an aioclient_mock."""
    mock.get(f"{API}/public/tenants/demo/config", json={"success": True, "data": {"slug": "demo-net", "appName": "Demo Net"}})
    mock.post(f"{API}/drivers/auth/login", json=LOGIN)
    mock.get(f"{API}/drivers/locations", json={"success": True, "data": LOCATIONS})
    mock.get(f"{API}/drivers/locations/loc-1/stations", json={"success": True, "data": STATIONS})
    mock.get(f"{API}/drivers/stations/{STATION}", json={"success": True, "data": STATIONS[0]})
    if start is not None:
        mock.post(f"{API}/drivers/charging/remote-start/{STATION}", json={"success": True, "data": start})
    if live_session is not None:
        mock.get(f"{API}/drivers/charging/live?sessionId=sess-1", json={"success": True, "data": live_session})
    mock.get(f"{API}/drivers/charging/live", json={"success": True, "data": live_current})


def make_entry(hass, **option_overrides):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"drv_{STATION}",
        title="CDN31 (Cadence)",
        data={"api_base": API, "tenant_slug": "demo-net", "email": "a@b.c", "password": "pw",
              "driver_id": "drv", "location_id": "loc-1", "location_name": "Cadence",
              "station_id": STATION, "station_name": "CDN31", "connector_id": 1},
        options={"target_kwh": 60, "cable_sensor": "binary_sensor.car_cable",
                 "vehicle_tracker": "device_tracker.car", "charge_zone": "zone.home",
                 "notify_service": "notify.mobile_app_pat", "pay_timeout": 1, **option_overrides},
    )
    entry.add_to_hass(hass)
    return entry


def set_world(hass, *, at_home=True):
    lat, lon = hass.config.latitude, hass.config.longitude
    hass.states.async_set("zone.home", "0", {"latitude": lat, "longitude": lon, "radius": 100, "passive": False, "friendly_name": "Home"})
    hass.states.async_set("device_tracker.car", "home" if at_home else "not_home",
                          {"latitude": lat if at_home else lat + 1, "longitude": lon, "gps_accuracy": 5})
    hass.states.async_set("binary_sensor.car_cable", "off")


def no_sleep():
    return patch("custom_components.scaleev.charging._pause", return_value=None)
