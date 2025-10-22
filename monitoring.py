"""Monitoring utilities for API health and watchlist management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import datetime as _dt
from typing import Mapping, MutableMapping, Sequence

import httpx
from PySide6 import QtCore

from archeblow_service import Network
from api_keys import API_SERVICE_KEYS


def _current_timestamp() -> int:
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


@dataclass(slots=True)
class MonitoringEvent:
    """Represents a log entry recorded by the monitoring system."""

    timestamp: int
    level: str
    category: str
    source: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class MonitoringWatch:
    """Tracks a wallet placed under extended observation."""

    address: str
    network: Network
    created_at: int
    expires_at: int
    comment: str = ""


class WebhookNotifier:
    """Dispatches monitoring events to an optional webhook endpoint."""

    def __init__(
        self,
        endpoint: str | None,
        *,
        session: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._session = session

    async def send(self, event: MonitoringEvent) -> None:
        if not self._endpoint:
            return
        payload = {
            "timestamp": event.timestamp,
            "level": event.level,
            "category": event.category,
            "source": event.source,
            "message": event.message,
            "details": dict(event.details),
        }
        close_session = False
        session = self._session
        if session is None:
            timeout = httpx.Timeout(10.0, connect=5.0)
            session = httpx.AsyncClient(timeout=timeout)
            close_session = True
        try:
            await session.post(self._endpoint, json=payload)
        except httpx.HTTPError:
            # Swallow webhook delivery issues to avoid crashing the UI.
            return
        finally:
            if close_session:
                await session.aclose()


class MonitoringService(QtCore.QObject):
    """Aggregates monitoring events, API health, and watchlist data."""

    event_recorded = QtCore.Signal(object)
    watch_added = QtCore.Signal(object)

    def __init__(
        self,
        *,
        webhook_url: str | None = None,
        session: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__()
        self._events: list[MonitoringEvent] = []
        self._watches: MutableMapping[tuple[str, Network], MonitoringWatch] = {}
        self._api_state: MutableMapping[str, dict[str, object]] = {}
        self._webhook = (
            WebhookNotifier(webhook_url, session=session) if webhook_url else None
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def log(
        self,
        level: str,
        message: str,
        *,
        source: str,
        category: str = "general",
        details: Mapping[str, object] | None = None,
    ) -> MonitoringEvent:
        event = MonitoringEvent(
            timestamp=_current_timestamp(),
            level=level,
            category=category,
            source=source,
            message=message,
            details=dict(details or {}),
        )
        self._register_event(event)
        return event

    def record_api_error(
        self,
        service_id: str,
        message: str,
        *,
        address: str | None = None,
        network: Network | None = None,
        details: Mapping[str, object] | None = None,
    ) -> MonitoringEvent:
        service_name = self._resolve_service_name(service_id)
        payload = dict(details or {})
        if address:
            payload["address"] = address
        if network:
            payload["network"] = network.value
        payload["service_id"] = service_id
        payload.setdefault("service_name", service_name)
        state = self._api_state.setdefault(
            service_id,
            {
                "service_id": service_id,
                "service_name": service_name,
                "status": "ok",
                "failures": 0,
                "last_error": None,
                "last_error_message": None,
                "last_success": None,
            },
        )
        state["status"] = "error"
        state["failures"] = int(state.get("failures", 0)) + 1
        state["last_error"] = _current_timestamp()
        state["last_error_message"] = message
        state["last_message"] = message
        event = self.log(
            "error",
            message,
            source=service_id,
            category="api",
            details=payload,
        )
        return event

    def record_api_success(
        self,
        service_id: str,
        message: str,
        *,
        address: str | None = None,
        network: Network | None = None,
        details: Mapping[str, object] | None = None,
    ) -> MonitoringEvent | None:
        service_name = self._resolve_service_name(service_id)
        payload = dict(details or {})
        if address:
            payload["address"] = address
        if network:
            payload["network"] = network.value
        payload["service_id"] = service_id
        payload.setdefault("service_name", service_name)
        state = self._api_state.setdefault(
            service_id,
            {
                "service_id": service_id,
                "service_name": service_name,
                "status": "ok",
                "failures": 0,
                "last_error": None,
                "last_error_message": None,
                "last_success": None,
                "last_message": None,
            },
        )
        state["status"] = "ok"
        state["last_success"] = _current_timestamp()
        state["last_message"] = message
        if state.get("failures", 0):
            # Emit an informational event only when recovering from failures.
            event = self.log(
                "info",
                message,
                source=service_id,
                category="api",
                details=payload,
            )
            return event
        return None

    def schedule_watch(
        self,
        address: str,
        network: Network,
        *,
        days: int = 30,
        comment: str | None = None,
    ) -> MonitoringWatch:
        now = _current_timestamp()
        expires_at = now + int(days * 86_400)
        key = (address.lower(), network)
        watch = MonitoringWatch(
            address=address,
            network=network,
            created_at=now,
            expires_at=expires_at,
            comment=comment or "",
        )
        self._watches[key] = watch
        self.watch_added.emit(watch)
        self.log(
            "info",
            f"Мониторинг включен для адреса {address} ({network.name.upper()}).",
            source="monitoring",
            category="watch",
            details={
                "address": address,
                "network": network.value,
                "expires_at": expires_at,
                "days": days,
                "comment": watch.comment,
                "service_name": "Система мониторинга",
            },
        )
        return watch

    def active_watches(self) -> Sequence[MonitoringWatch]:
        now = _current_timestamp()
        active = [watch for watch in self._watches.values() if watch.expires_at >= now]
        return sorted(active, key=lambda item: item.expires_at)

    def watch_for(self, address: str, network: Network) -> Sequence[MonitoringWatch]:
        normalized = address.lower()
        watches = [
            watch
            for (addr, net), watch in self._watches.items()
            if addr == normalized and net == network and watch.expires_at >= _current_timestamp()
        ]
        return sorted(watches, key=lambda item: item.expires_at)

    def recent_events(self, limit: int = 10) -> Sequence[MonitoringEvent]:
        if not self._events:
            return []
        return list(reversed(self._events[-limit:]))

    def events_for(
        self,
        address: str,
        network: Network,
        limit: int = 5,
    ) -> Sequence[MonitoringEvent]:
        normalized = address.lower()
        matched: list[MonitoringEvent] = []
        for event in reversed(self._events):
            event_address = str(event.details.get("address", "")).lower()
            event_network = event.details.get("network")
            if event_address == normalized and event_network == network.value:
                matched.append(event)
                if len(matched) >= limit:
                    break
        return matched

    def api_status_snapshot(self) -> Sequence[dict[str, object]]:
        records = []
        for state in self._api_state.values():
            records.append(dict(state))
        records.sort(key=lambda item: str(item.get("service_name", item.get("service_id", ""))))
        return records

    def active_api_incidents(self) -> Sequence[dict[str, object]]:
        return [
            dict(state)
            for state in self._api_state.values()
            if state.get("status") == "error"
        ]

    def status_summary(self) -> str:
        incidents = self.active_api_incidents()
        if not incidents:
            return "API стабильны"
        summary_parts = [
            f"{item.get('service_name', item['service_id'])} ({item.get('failures', 0)} сбоев)"
            for item in incidents
        ]
        return ", ".join(summary_parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _register_event(self, event: MonitoringEvent) -> None:
        self._events.append(event)
        if len(self._events) > 200:
            self._events[:] = self._events[-200:]
        self.event_recorded.emit(event)
        self._dispatch_webhook(event)

    def _dispatch_webhook(self, event: MonitoringEvent) -> None:
        if not self._webhook:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(self._webhook.send(event))
        else:
            asyncio.run(self._webhook.send(event))

    @staticmethod
    def _resolve_service_name(service_id: str) -> str:
        entry = API_SERVICE_KEYS.get(service_id)
        if entry:
            return entry.display_name
        return service_id


__all__ = [
    "MonitoringEvent",
    "MonitoringService",
    "MonitoringWatch",
]
