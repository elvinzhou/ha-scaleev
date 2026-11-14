"""Charging hours (time-of-use) and the start link."""
from datetime import datetime, time

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from custom_components.scaleev import _in_window

from .common import LIVE_AUTH, make_entry, mock_api, no_sleep, set_world
from .test_charging import PENDING_START, _plug_in, _poll, _setup, _starts

STATUS = "sensor.cdn31_start_status"
HOURS = {"window_start": "21:00:00", "window_end": "06:00:00"}


@pytest.fixture(autouse=True)
def _fast_polls():
    with no_sleep():
        yield


def _at(freezer, hour, minute=0):
    freezer.move_to(datetime(2026, 11, 14, hour, minute, tzinfo=dt_util.DEFAULT_TIME_ZONE))


async def _tick(hass, entry):
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    if entry.runtime_data._task:
        await entry.runtime_data._task


def test_window_spanning_midnight():
    night = (time(21), time(6))
    assert _in_window(time(23), night) and _in_window(time(2), night) and _in_window(time(21), night)
    assert not _in_window(time(6), night) and not _in_window(time(14), night)
    day = (time(9), time(17))
    assert _in_window(time(12), day) and not _in_window(time(17), day) and not _in_window(time(3), day)


async def test_plug_in_before_charging_hours_waits(hass, aioclient_mock, freezer):
    _at(freezer, 18)
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock, **HOURS)
    await _plug_in(hass, entry)
    assert not _starts(aioclient_mock) and not notify
    state = hass.states.get(STATUS)
    assert state.state == "scheduled" and state.attributes["detail"] == "Starts at 21:00"
    _at(freezer, 20, 30)
    await _tick(hass, entry)
    assert not _starts(aioclient_mock)
    _at(freezer, 21)
    await _tick(hass, entry)
    assert len(_starts(aioclient_mock)) == 1
    assert notify[0].data["title"] == "ScaleEV: payment needed"
    assert hass.states.get(STATUS).state == "started"


async def test_plug_in_during_charging_hours_starts_now(hass, aioclient_mock, freezer):
    _at(freezer, 23)
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock, **HOURS)
    await _plug_in(hass, entry)
    assert len(_starts(aioclient_mock)) == 1


async def test_unplugging_cancels_the_wait(hass, aioclient_mock, freezer):
    _at(freezer, 18)
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock, **HOURS)
    await _plug_in(hass, entry)
    hass.states.async_set("binary_sensor.car_cable", "off")
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "idle"
    await _poll(hass, aioclient_mock, entry, "Available", start=PENDING_START, live_session=LIVE_AUTH)
    _at(freezer, 21)
    await _tick(hass, entry)
    assert not _starts(aioclient_mock) and not notify


async def test_button_ignores_charging_hours(hass, aioclient_mock, freezer):
    _at(freezer, 18)
    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock, **HOURS)
    await hass.services.async_call("button", "press", {"entity_id": "button.cdn31_start_charging"}, blocking=True)
    await entry.runtime_data._task
    assert len(_starts(aioclient_mock)) == 1


async def test_payment_messages_carry_the_start_link(hass, aioclient_mock):
    mock_api(aioclient_mock, start=PENDING_START, live_session={**LIVE_AUTH, "paymentStatus": "pending"})
    email = async_mock_service(hass, "notify", "email_me")
    entry, _ = await _setup(hass, aioclient_mock, notify_service="notify.email_me")
    await _plug_in(hass, entry)
    link = entry.runtime_data.start_link
    assert "/api/scaleev/start/" in link
    assert "https://checkout.example/pay" in email[0].data["message"] and link in email[0].data["message"]
    assert email[-1].data["title"] == "ScaleEV: charging did not start" and link in email[-1].data["message"]


async def test_start_link_redirects_to_checkout(hass, aioclient_mock, hass_client_no_auth):
    """Opening the link starts a session and lands on its payment page."""
    import asyncio
    from unittest.mock import patch

    mock_api(aioclient_mock, start=PENDING_START, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock)
    client = await hass_client_no_auth()
    path = "/api/scaleev/start/" + entry.runtime_data.link_token
    release = asyncio.Event()

    async def _held(_seconds):
        await release.wait()

    with patch("custom_components.scaleev.charging._pause", new=_held):
        first = await client.get(path, allow_redirects=False)
        again = await client.get(path, allow_redirects=False)  # e.g. a mail scanner, then the person
        release.set()
        await entry.runtime_data._task
    assert first.status == 302 and first.headers["Location"] == "https://checkout.example/pay"
    assert again.status == 302 and again.headers["Location"] == "https://checkout.example/pay"
    assert len(_starts(aioclient_mock)) == 1


async def test_start_link_without_payment_and_bad_token(hass, aioclient_mock, hass_client_no_auth):
    mock_api(aioclient_mock, start={"sessionId": "sess-1", "paymentStatus": "authorized"}, live_session=LIVE_AUTH)
    entry, notify = await _setup(hass, aioclient_mock)
    client = await hass_client_no_auth()
    bad = await client.get("/api/scaleev/start/" + "0" * 40, allow_redirects=False)
    assert bad.status == 404 and not _starts(aioclient_mock)
    ok = await client.get("/api/scaleev/start/" + entry.runtime_data.link_token, allow_redirects=False)
    assert ok.status == 200 and "Charging has started" in await ok.text()
