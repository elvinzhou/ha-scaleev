import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import async_mock_service

from .common import API, LIVE_AUTH, STATION, make_entry, mock_api, no_sleep, set_world

PENDING_START = {"sessionId": "sess-1", "checkoutUrl": "https://checkout.example/pay", "paymentStatus": "pending"}


@pytest.fixture(autouse=True)
def _fast_polls():
    with no_sleep():
        yield


async def _setup(hass, mock, **kw):
    notify = async_mock_service(hass, "notify", "mobile_app_pat")
    set_world(hass, at_home=kw.pop("at_home", True))
    entry = make_entry(hass, **kw)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, notify


async def _plug_in(hass, entry):
    hass.states.async_set("binary_sensor.car_cable", "on")
    await hass.async_block_till_done()
    task = entry.runtime_data._task
    if task:
        await task


async def test_auto_start_with_payment(hass, aioclient_mock):
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock)
    await _plug_in(hass, entry)
    titles = [c.data.get("title") for c in notify]
    assert titles == ["ScaleEV: payment needed", None, "ScaleEV: charging started"]
    pay = notify[0].data["data"]
    assert pay["actions"][0]["uri"] == "https://checkout.example/pay" and pay["tag"] == "scaleev_pay"
    assert notify[1].data["message"] == "clear_notification"
    start_call = [c for c in aioclient_mock.mock_calls if c[0] == "POST" and "remote-start" in str(c[1])][0]
    assert str(start_call[1]).endswith(f"/remote-start/{STATION}")
    assert start_call[2] == {"connectorId": 1, "priceFlowType": "kwh", "targetKwh": 60}


async def test_payment_timeout(hass, aioclient_mock):
    pending = {**LIVE_AUTH, "paymentStatus": "pending"}
    mock_api(aioclient_mock, start=PENDING_START, live_session=pending)
    entry, notify = await _setup(hass, aioclient_mock)
    await _plug_in(hass, entry)
    assert notify[-1].data["title"] == "ScaleEV: charging did not start"
    assert "Payment was not completed" in notify[-1].data["message"]


async def test_saved_payment_no_checkout(hass, aioclient_mock):
    mock_api(aioclient_mock, start={"sessionId": "sess-1", "paymentStatus": "authorized"}, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock)
    await _plug_in(hass, entry)
    assert [c.data.get("title") for c in notify] == ["ScaleEV: charging started"]


async def test_start_rejected(hass, aioclient_mock):
    mock_api(aioclient_mock)
    aioclient_mock.post(f"{API}/drivers/charging/remote-start/{STATION}", status=400,
                        json={"success": False, "message": "Connector not available"})
    entry, notify = await _setup(hass, aioclient_mock)
    await _plug_in(hass, entry)
    assert notify[-1].data["title"] == "ScaleEV: could not start charging"
    assert "Connector not available" in notify[-1].data["message"]


async def test_outside_zone_does_nothing(hass, aioclient_mock):
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock, at_home=False)
    await _plug_in(hass, entry)
    assert not notify
    assert not [c for c in aioclient_mock.mock_calls if "remote-start" in str(c[1])]


async def test_auto_start_switch_off(hass, aioclient_mock):
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.cdn31_auto_start_on_plug_in"}, blocking=True)
    await _plug_in(hass, entry)
    assert not notify


async def test_already_charging_is_skipped(hass, aioclient_mock):
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH, live_current=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock)
    await _plug_in(hass, entry)
    assert not notify
    assert not [c for c in aioclient_mock.mock_calls if "remote-start" in str(c[1])]


async def test_button_starts_without_cable(hass, aioclient_mock):
    mock_api(aioclient_mock, start={"sessionId": "sess-1", "paymentStatus": "authorized"}, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock)
    await hass.services.async_call("button", "press", {"entity_id": "button.cdn31_start_charging"}, blocking=True)
    await entry.runtime_data._task
    assert notify[-1].data["title"] == "ScaleEV: charging started"


async def test_sensors(hass, aioclient_mock):
    mock_api(aioclient_mock, live_current=LIVE_AUTH)
    entry, _ = await _setup(hass, aioclient_mock)
    assert hass.states.get("sensor.cdn31_connector_status").state == "Preparing"
    assert hass.states.get("sensor.cdn31_session_status").state == "in-progress"
    assert hass.states.get("sensor.cdn31_session_energy").state == "1.5"


async def test_bad_password_triggers_reauth(hass, aioclient_mock):
    mock_api(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API}/drivers/auth/login", status=401, json={"success": False, "message": "bad"})
    entry = make_entry(hass)
    set_world(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())
