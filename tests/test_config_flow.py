from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.scaleev.const import DOMAIN

from .common import API, LIVE_AUTH, STATION, make_entry, mock_api, set_world


async def _start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_full_flow(hass, aioclient_mock):
    mock_api(aioclient_mock)
    async_mock_service(hass, "notify", "mobile_app_pat")
    set_world(hass)
    r = await _start(hass)
    assert r["step_id"] == "user"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"link": "https://demo.scaleev.scaletech.xyz/map", "api_base": API})
    assert r["step_id"] == "login"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.c", "password": "pw"})
    # single location is skipped, so we land on the station choice
    assert r["step_id"] == "station"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"station_id": STATION})
    assert r["step_id"] == "settings"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "target_kwh": 60, "cable_sensor": "binary_sensor.car_cable", "vehicle_tracker": "device_tracker.car",
        "radius": 200, "notify_service": "notify.mobile_app_pat", "pay_timeout": 10})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "CDN31 (Cadence)"
    assert r["data"]["location_latitude"] == 37.634166 and r["data"]["location_longitude"] == -122.054248
    assert r["options"]["radius"] == 200 and "charge_zone" not in r["options"]
    assert r["data"]["tenant_slug"] == "demo-net" and r["data"]["station_id"] == STATION
    assert r["data"]["connector_id"] == 1
    assert r["options"]["target_kwh"] == 60


async def test_invalid_auth(hass, aioclient_mock):
    aioclient_mock.get(f"{API}/public/tenants/demo/config", json={"success": True, "data": {"slug": "demo-net"}})
    aioclient_mock.post(f"{API}/drivers/auth/login", status=401, json={"success": False, "message": "bad"})
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"link": "demo.scaleev.scaletech.xyz", "api_base": API})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.c", "password": "x"})
    assert r["errors"] == {"base": "invalid_auth"}


async def test_unknown_host_needs_api_base(hass, aioclient_mock):
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"link": "https://charge.example.com/map"})
    assert r["errors"] == {"api_base": "api_base_required"}


async def test_unknown_network(hass, aioclient_mock):
    aioclient_mock.get(f"{API}/public/tenants/nope/config", status=404, json={"success": False})
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"link": "https://nope.scaleev.scaletech.xyz", "api_base": API})
    assert r["errors"] == {"base": "cannot_connect"}


async def test_duplicate_station_aborts(hass, aioclient_mock):
    mock_api(aioclient_mock)
    make_entry(hass)
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"link": "https://demo.scaleev.scaletech.xyz", "api_base": API})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.c", "password": "pw"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"station_id": STATION})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured"


async def test_options_flow(hass, aioclient_mock):
    mock_api(aioclient_mock)
    async_mock_service(hass, "notify", "mobile_app_pat")
    set_world(hass)
    entry = make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["step_id"] == "init"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "target_kwh": 75, "cable_sensor": "binary_sensor.car_cable", "vehicle_tracker": "device_tracker.car",
        "radius": 300, "charge_zone": "zone.home", "notify_service": "notify.mobile_app_pat", "pay_timeout": 5})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["target_kwh"] == 75 and entry.options["charge_zone"] == "zone.home"
    assert entry.options["radius"] == 300


async def test_reauth(hass, aioclient_mock):
    mock_api(aioclient_mock)
    entry = make_entry(hass)
    r = await entry.start_reauth_flow(hass)
    assert r["step_id"] == "reauth_confirm"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"password": "new"})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"


async def test_zone_required_without_coordinates(hass, aioclient_mock):
    mock_api(aioclient_mock, locations=[{"id": "loc-1", "name": "Cadence"}])
    async_mock_service(hass, "notify", "mobile_app_pat")
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"link": "https://demo.scaleev.scaletech.xyz", "api_base": API})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": "a@b.c", "password": "pw"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"station_id": STATION})
    base = {"target_kwh": 60, "cable_sensor": "binary_sensor.car_cable", "vehicle_tracker": "device_tracker.car",
            "radius": 150, "notify_service": "notify.mobile_app_pat", "pay_timeout": 10}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], base)
    assert r["errors"] == {"charge_zone": "zone_required"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**base, "charge_zone": "zone.home"})
    assert r["type"] is FlowResultType.CREATE_ENTRY and r["data"]["location_latitude"] is None


def test_charging_hours_need_both_times():
    from custom_components.scaleev.config_flow import _settings_errors

    assert _settings_errors({"window_start": "21:00:00"}, True) == {"window_end": "window_incomplete"}
    assert _settings_errors({"window_end": "06:00:00"}, True) == {"window_start": "window_incomplete"}
    assert _settings_errors({"window_start": "21:00:00", "window_end": "21:00:00"}, True) == {"window_end": "window_empty"}
    assert _settings_errors({"window_start": "21:00:00", "window_end": "06:00:00"}, True) == {}
    assert _settings_errors({}, True) == {}
