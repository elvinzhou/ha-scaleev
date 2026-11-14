"""The start link: one tap in an email starts charging and lands on the payment page."""
from __future__ import annotations

import asyncio
import hmac
from html import escape

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    STATUS_ALREADY_CHARGING,
    STATUS_FAILED,
    STATUS_NOT_CONFIRMED,
    STATUS_PAYMENT_TIMEOUT,
    STATUS_STARTED,
)

WAIT_SECONDS = 30  # for ScaleEV to answer the start request
MESSAGES = {
    STATUS_STARTED: "Charging has started.",
    STATUS_ALREADY_CHARGING: "A charging session is already in progress.",
    STATUS_PAYMENT_TIMEOUT: "Payment was not completed in time. Open the link again to retry.",
    STATUS_NOT_CONFIRMED: "The start could not be confirmed. Check the charger, or open the link again to retry.",
}


def _page(text: str, status: int = 200) -> web.Response:
    body = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>ScaleEV</title><body style='font:18px system-ui;margin:3em auto;max-width:28em;padding:0 1em'>"
        f"<h1 style='font-size:1.3em'>ScaleEV</h1><p>{escape(text)}</p>"
    )
    return web.Response(text=body, content_type="text/html", status=status)


class StartLinkView(HomeAssistantView):
    """GET /api/scaleev/start/<token>.

    Opened from an email or message, so it cannot carry a Home Assistant
    login; the unguessable token in the path is the credential. Opening it
    twice is safe: while an attempt is waiting for payment it just leads to
    that attempt's checkout page.
    """

    url = f"/api/{DOMAIN}/start/{{token}}"
    name = f"api:{DOMAIN}:start"
    requires_auth = False

    async def get(self, request: web.Request, token: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        manager = next(
            (
                entry.runtime_data
                for entry in hass.config_entries.async_entries(DOMAIN)
                if entry.state is ConfigEntryState.LOADED and hmac.compare_digest(entry.runtime_data.link_token, token)
            ),
            None,
        )
        if manager is None:
            return _page("This link is not valid any more.", 404)
        if not manager.running:
            manager.async_trigger(manual=True)
        for _ in range(WAIT_SECONDS * 4):
            if manager.checkout_url:
                raise web.HTTPFound(manager.checkout_url)
            if not manager.running:
                break
            await asyncio.sleep(0.25)
        if manager.status == STATUS_FAILED:
            return _page(f"Could not start charging: {manager.status_detail or 'unknown error'}")
        return _page(MESSAGES.get(manager.status, "Starting. You'll get a notification when charging begins."))
