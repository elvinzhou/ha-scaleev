"""Starts a charging session and keeps the driver informed."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import ScaleEVAuthError, ScaleEVClient, ScaleEVError, StartResult
from .const import (
    CONF_CONNECTOR_ID,
    CONF_EMAIL,
    CONF_NOTIFY_SERVICE,
    CONF_PASSWORD,
    CONF_PAY_TIMEOUT,
    CONF_STATION_ID,
    CONF_STATION_NAME,
    CONF_TARGET_KWH,
    DEFAULT_PAY_TIMEOUT,
    DOMAIN,
    NO_PAYMENT_WAIT,
    NOTIFICATION_TAG,
    PAID_STATUSES,
    POLL_SECONDS,
    STATUS_ALREADY_CHARGING,
    STATUS_AWAITING_PAYMENT,
    STATUS_FAILED,
    STATUS_IDLE,
    STATUS_NOT_CONFIRMED,
    STATUS_PAYMENT_TIMEOUT,
    STATUS_STARTED,
    STATUS_STARTING,
    UNPAID_STATUS,
)
from .coordinator import ScaleEVCoordinator

_LOGGER = logging.getLogger(__name__)


async def _pause(seconds: float) -> None:
    """Sleep between polls (patched out in tests)."""
    await asyncio.sleep(seconds)


class ChargeManager:
    """Runs one start-charging sequence at a time."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ScaleEVClient, coordinator: ScaleEVCoordinator
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.coordinator = coordinator
        self.auto_start = True
        self.status = STATUS_IDLE
        self.status_detail: str | None = None
        self._status_listeners: list[Callable[[], None]] = []
        self._task: asyncio.Task | None = None
        # The checkout link of the attempt in progress, while it awaits payment.
        self.checkout_url: str | None = None

    @property
    def link_token(self) -> str:
        """Secret in the start link. Stable across restarts; changes with the password."""
        seed = f"{self.entry.entry_id}:{self.entry.data[CONF_EMAIL]}:{self.entry.data[CONF_PASSWORD]}"
        return hashlib.sha256(seed.encode()).hexdigest()[:40]

    @property
    def start_link(self) -> str | None:
        """A link that starts charging and lands on the payment page; None if Home Assistant has no URL."""
        try:
            base = get_url(self.hass, prefer_external=True, allow_cloud=True)
        except NoURLAvailableError:
            return None
        return f"{base}/api/{DOMAIN}/start/{self.link_token}"

    @callback
    def async_add_status_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._status_listeners.append(listener)
        return lambda: self._status_listeners.remove(listener)

    @callback
    def async_set_status(self, status: str, detail: str | None = None) -> None:
        """Record the outcome of a start attempt for the Start status sensor."""
        if (status, detail) == (self.status, self.status_detail):
            return
        self.status = status
        self.status_detail = detail
        _LOGGER.info("ScaleEV start status: %s%s", status, f" ({detail})" if detail else "")
        for listener in list(self._status_listeners):
            listener()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @callback
    def async_trigger(self, manual: bool) -> bool:
        """Start the sequence in the background; False if one is already running."""
        if self.running:
            return False
        self._task = self.entry.async_create_background_task(
            self.hass, self._run(manual), name=f"scaleev start {self.entry.entry_id}"
        )
        return True

    async def _run(self, manual: bool) -> None:
        self.async_set_status(STATUS_STARTING)
        try:
            await self._start(manual)
        except ScaleEVAuthError:
            self.async_set_status(STATUS_FAILED, "Login rejected")
            await self._notify("ScaleEV: login failed", "Your ScaleEV credentials were rejected. Re-enter your password in Home Assistant.")
            self.entry.async_start_reauth(self.hass)
        except ScaleEVError as err:
            self.async_set_status(STATUS_FAILED, str(err))
            await self._notify("ScaleEV: could not start charging", f"{err} (station {self._name}).")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let the background task die silently
            _LOGGER.exception("Unexpected error starting ScaleEV session")
            self.async_set_status(STATUS_FAILED, "Unexpected error, see the log")
            await self._notify("ScaleEV: could not start charging", "Unexpected error, see the Home Assistant log.")
        finally:
            self.checkout_url = None

    @property
    def _name(self) -> str:
        return self.entry.data.get(CONF_STATION_NAME) or self.entry.data[CONF_STATION_ID]

    async def _start(self, manual: bool) -> None:
        opts = self.entry.options
        target = opts[CONF_TARGET_KWH]
        current = await self.client.async_live()
        if current and current.get("paymentStatus") == UNPAID_STATUS:
            # Left over from an attempt that was never paid for. It is not
            # charging, so it must not block a new start.
            _LOGGER.info("Ignoring unpaid ScaleEV session %s and starting a new one", current.get("id"))
        elif current:
            self.async_set_status(STATUS_ALREADY_CHARGING, f"Session is {current.get('status', 'active')}")
            if manual:
                await self._notify("ScaleEV: already charging", f"A session is already in progress at {self._name}.")
            return

        result = await self.client.async_remote_start(
            self.entry.data[CONF_STATION_ID], self.entry.data[CONF_CONNECTOR_ID], target
        )
        needs_payment = bool(result.checkout_url) and result.payment_status == "pending"
        if needs_payment:
            self.checkout_url = result.checkout_url
            self.async_set_status(STATUS_AWAITING_PAYMENT)
            await self._notify_payment(result, target)
            wait_seconds = float(opts.get(CONF_PAY_TIMEOUT, DEFAULT_PAY_TIMEOUT)) * 60
        else:
            wait_seconds = NO_PAYMENT_WAIT

        live = await self._wait_authorized(result.session_id, wait_seconds)
        if needs_payment:
            await self._notify("", "clear_notification", clear=True)
        await self.coordinator.async_request_refresh()
        if live:
            self.async_set_status(STATUS_STARTED)
            await self._notify("ScaleEV: charging started", f"{self._name} is {live.get('status', 'started')} ({target} kWh target).")
        elif needs_payment:
            self.async_set_status(STATUS_PAYMENT_TIMEOUT)
            again = self.start_link
            await self._notify(
                "ScaleEV: charging did not start",
                f"Payment was not completed within {int(wait_seconds // 60)} minutes.",
                data={"actions": [{"action": "URI", "title": "Try again", "uri": again}]} if again else None,
                links=[("Try again", again)] if again else None,
            )
        else:
            self.async_set_status(STATUS_NOT_CONFIRMED)
            await self._notify("ScaleEV: charging did not start", "The session could not be confirmed.")

    async def _wait_authorized(self, session_id: str, seconds: float) -> dict[str, Any] | None:
        polls = max(1, int(seconds / POLL_SECONDS))
        for _ in range(polls):
            await _pause(POLL_SECONDS)
            try:
                live = await self.client.async_live(session_id)
            except ScaleEVAuthError:
                raise
            except ScaleEVError as err:
                _LOGGER.debug("Transient error while waiting for authorization: %s", err)
                continue
            if live and live.get("paymentStatus") in PAID_STATUSES:
                return live
        return None

    async def _notify_payment(self, result: StartResult, target: float) -> None:
        links = [("Pay now", result.checkout_url)]
        if again := self.start_link:
            # Checkout links expire; this one always leads to a current one.
            links.append(("If that link has expired, get a new one", again))
        await self._notify(
            "ScaleEV: payment needed",
            f"Authorize payment to start charging ({target} kWh).",
            data={
                "tag": NOTIFICATION_TAG,
                "clickAction": result.checkout_url,
                "url": result.checkout_url,
                "actions": [{"action": "URI", "title": "Pay now", "uri": result.checkout_url}]
                + ([{"action": "URI", "title": "New payment link", "uri": again}] if again else []),
            },
            links=links,
        )

    async def _notify(
        self,
        title: str,
        message: str,
        data: dict | None = None,
        clear: bool = False,
        links: list[tuple[str, str]] | None = None,
    ) -> None:
        """Send through the configured notify action or entity, else a persistent notification.

        Only the companion app understands the tap actions in ``data`` and the
        clear command. Everything else (email, Telegram, ...) gets the links in
        the message text, and nothing at all for a clear.
        """
        target = self.entry.options.get(CONF_NOTIFY_SERVICE) or ""
        service = target.removeprefix("notify.")
        mobile = service.startswith("mobile_app_")
        if clear and not mobile:
            return
        plain = message + "".join(f"\n{label}: {url}" for label, url in links or [])
        try:
            if service and self.hass.services.has_service("notify", service):
                payload: dict[str, Any] = {"message": message if mobile else plain}
                if clear:
                    payload["data"] = {"tag": NOTIFICATION_TAG}
                else:
                    payload["title"] = title
                    if data and mobile:
                        payload["data"] = data
                await self.hass.services.async_call("notify", service, payload, blocking=True)
                return
            if target.startswith("notify.") and self.hass.states.get(target) is not None:
                await self.hass.services.async_call(
                    "notify", "send_message", {"entity_id": target, "title": title, "message": plain}, blocking=True
                )
                return
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Notify target %s failed", target)
        if not clear:
            markdown = " ".join([message, *(f"[{label}]({url})" for label, url in links or [])])
            persistent_notification.async_create(self.hass, markdown, title=title)
