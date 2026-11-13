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
    set_world(hass, at_home=kw.pop("at_home", True), gps=kw.pop("gps", True))
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


async def test_zone_override_is_used_instead_of_location(hass, aioclient_mock):
    """Location coordinates are far away, but the chosen zone contains the car."""
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    notify = async_mock_service(hass, "notify", "mobile_app_pat")
    set_world(hass)
    entry = make_entry(hass, charge_zone="zone.home")
    hass.config_entries.async_update_entry(entry, data={**entry.data, "location_latitude": 0.0, "location_longitude": 0.0})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _plug_in(hass, entry)
    assert notify[0].data["title"] == "ScaleEV: payment needed"


async def test_far_from_location_does_nothing(hass, aioclient_mock):
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock, radius=25)
    hass.states.async_set("device_tracker.car", "not_home",
                          {"latitude": hass.config.latitude + 0.01, "longitude": hass.config.longitude, "gps_accuracy": 5})
    await _plug_in(hass, entry)
    assert not notify


async def test_radius_boundary(hass, aioclient_mock):
    """~111 m away is inside a 150 m radius but outside a 50 m one."""
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock, radius=50)
    hass.states.async_set("device_tracker.car", "not_home",
                          {"latitude": hass.config.latitude + 0.001, "longitude": hass.config.longitude, "gps_accuracy": 0})
    await _plug_in(hass, entry)
    assert not notify
    hass.config_entries.async_update_entry(entry, options={**entry.options, "radius": 150})
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.car_cable", "off")
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    await _plug_in(hass, entry)
    assert notify and notify[0].data["title"] == "ScaleEV: payment needed"


async def test_no_gps_and_no_zone_does_nothing(hass, aioclient_mock):
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock, gps=False)
    await _plug_in(hass, entry)
    assert not notify


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


def _starts(mock):
    return [c for c in mock.mock_calls if c[0] == "POST" and "remote-start" in str(c[1])]


async def test_cable_sensor_dropouts_do_not_restart(hass, aioclient_mock):
    """on -> unavailable -> on is the car's integration blinking, not a plug-in."""
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock)
    await _plug_in(hass, entry)
    assert len(_starts(aioclient_mock)) == 1
    for state in ("unavailable", "on", "unknown", "on"):
        hass.states.async_set("binary_sensor.car_cable", state)
        await hass.async_block_till_done()
    assert len(_starts(aioclient_mock)) == 1


async def test_plug_in_seen_through_a_dropout(hass, aioclient_mock):
    """off -> unavailable -> on is still a plug-in."""
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock)
    hass.states.async_set("binary_sensor.car_cable", "unavailable")
    await hass.async_block_till_done()
    await _plug_in(hass, entry)
    assert len(_starts(aioclient_mock)) == 1


async def test_already_plugged_in_at_startup_does_not_start(hass, aioclient_mock):
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    notify = async_mock_service(hass, "notify", "mobile_app_pat")
    set_world(hass)
    hass.states.async_set("binary_sensor.car_cable", "unavailable")
    entry = make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _plug_in(hass, entry)
    assert not _starts(aioclient_mock) and not notify


async def test_button_state_survives_api_outage(hass, aioclient_mock):
    """A failed poll must not make the button blink (Activity shows that as a press)."""
    mock_api(aioclient_mock, start={"sessionId": "sess-1", "paymentStatus": "authorized"}, live_session=LIVE_AUTH)
    entry, _ = await _setup(hass, aioclient_mock)
    changes = []
    hass.bus.async_listen("state_changed", lambda e: changes.append(e.data["entity_id"]))
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/drivers/charging/live", status=500, json={})
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert "sensor.cdn31_connector_status" in changes
    assert "button.cdn31_start_charging" not in changes
    assert "switch.cdn31_auto_start_on_plug_in" not in changes
    assert hass.states.get("sensor.cdn31_connector_status").state == "unavailable"


async def test_unpaid_leftover_session_does_not_block(hass, aioclient_mock):
    """A session abandoned at checkout is not charging; plugging in starts a new one."""
    leftover = {**LIVE_AUTH, "id": "old", "paymentStatus": "pending"}
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH, live_current=leftover)
    entry, notify = await _setup(hass, aioclient_mock)
    await _plug_in(hass, entry)
    assert len(_starts(aioclient_mock)) == 1
    assert notify[0].data["title"] == "ScaleEV: payment needed"


async def test_start_status_explains_skips(hass, aioclient_mock):
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock, at_home=False)
    status = "sensor.cdn31_start_status"
    assert hass.states.get(status).state == "idle"
    await _plug_in(hass, entry)
    assert hass.states.get(status).state == "away"
    assert "device_tracker.car" in hass.states.get(status).attributes["detail"]
    set_world(hass)
    await _plug_in(hass, entry)
    assert hass.states.get(status).state == "started"


async def test_start_status_already_charging_and_failed(hass, aioclient_mock):
    mock_api(aioclient_mock, live_current=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock)
    await _plug_in(hass, entry)
    assert hass.states.get("sensor.cdn31_start_status").state == "already_charging"
    aioclient_mock.clear_requests()
    mock_api(aioclient_mock)
    aioclient_mock.post(f"{API}/drivers/charging/remote-start/{STATION}", status=400,
                        json={"success": False, "message": "Connector not available"})
    hass.states.async_set("binary_sensor.car_cable", "off")
    await _plug_in(hass, entry)
    state = hass.states.get("sensor.cdn31_start_status")
    assert state.state == "failed" and state.attributes["detail"] == "Connector not available"


async def test_button_press_while_running_is_reported(hass, aioclient_mock):
    import asyncio
    from unittest.mock import patch

    from homeassistant.exceptions import HomeAssistantError

    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, _ = await _setup(hass, aioclient_mock)
    release = asyncio.Event()

    async def _held(_seconds):
        await release.wait()

    with patch("custom_components.scaleev.charging._pause", new=_held):
        hass.states.async_set("binary_sensor.car_cable", "on")
        await hass.async_block_till_done()
        assert hass.states.get("sensor.cdn31_start_status").state == "awaiting_payment"
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call("button", "press", {"entity_id": "button.cdn31_start_charging"}, blocking=True)
        release.set()
        await entry.runtime_data._task
    assert hass.states.get("sensor.cdn31_start_status").state == "started"


async def test_email_style_notify_gets_link_in_text(hass, aioclient_mock):
    """Anything that isn't the companion app can't use tap actions or clear commands."""
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    email = async_mock_service(hass, "notify", "email_me")
    entry, _ = await _setup(hass, aioclient_mock, notify_service="notify.email_me")
    await _plug_in(hass, entry)
    assert [c.data["title"] for c in email] == ["ScaleEV: payment needed", "ScaleEV: charging started"]
    assert "https://checkout.example/pay" in email[0].data["message"]
    assert "data" not in email[0].data


async def test_notify_entity(hass, aioclient_mock):
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    sent = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("notify.my_phone", "unknown")
    entry, _ = await _setup(hass, aioclient_mock, notify_service="notify.my_phone")
    await _plug_in(hass, entry)
    assert sent[0].data["entity_id"] == "notify.my_phone"
    assert "https://checkout.example/pay" in sent[0].data["message"]
    assert len(sent) == 2


async def test_missing_notify_action_falls_back_with_link(hass, aioclient_mock):
    from unittest.mock import patch

    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    with patch("custom_components.scaleev.charging.persistent_notification.async_create") as create:
        entry, _ = await _setup(hass, aioclient_mock, notify_service="notify.gone")
        await _plug_in(hass, entry)
    assert "[Pay now](https://checkout.example/pay)" in create.call_args_list[0].args[1]
