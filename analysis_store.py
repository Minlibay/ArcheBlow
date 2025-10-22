"""Shared in-memory store for completed wallet analyses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from PySide6 import QtCore

from archeblow_service import AddressAnalysisResult, Network, TransactionHop
from ai_analyst import AnalystBriefing


@dataclass(frozen=True, slots=True)
class TransactionDigest:
    """Compact representation of a hop relevant to the analysed address."""

    analysis_address: str
    network: Network
    tx_hash: str
    direction: str
    counterpart: str
    amount: float
    timestamp: int


class AnalysisStore(QtCore.QObject):
    """Keeps completed analyses and exposes derived aggregates."""

    result_added = QtCore.Signal(AddressAnalysisResult)

    def __init__(self) -> None:
        super().__init__()
        self._results: list[AddressAnalysisResult] = []
        self._briefings: list[AnalystBriefing] = []

    def add_result(
        self,
        result: AddressAnalysisResult,
        *,
        briefing: AnalystBriefing | None = None,
    ) -> None:
        """Persist ``result`` and notify subscribers."""

        self._results.append(result)
        if briefing is not None:
            self._briefings.append(briefing)
        self.result_added.emit(result)

    def results(self) -> list[AddressAnalysisResult]:
        """Return a copy of all stored analyses."""

        return list(self._results)

    def briefings(self) -> list[AnalystBriefing]:
        """Return a copy of analyst briefings."""

        return list(self._briefings)

    def set_briefing(self, briefing: AnalystBriefing) -> None:
        """Append an analyst briefing to the history."""

        self._briefings.append(briefing)

    def briefing_for(self, address: str, network: Network) -> AnalystBriefing | None:
        """Return the latest briefing for the provided address and network."""

        normalized = address.lower()
        for briefing in reversed(self._briefings):
            if briefing.address.lower() == normalized and briefing.network == network:
                return briefing
        return None

    def recent_briefings(self, limit: int = 5) -> Sequence[AnalystBriefing]:
        """Return the most recent analyst briefings."""

        if not self._briefings:
            return []
        return list(reversed(self._briefings[-limit:]))

    def analyst_alerts(self, limit: int = 5) -> Sequence[str]:
        """Return the latest alerts raised by the analyst."""

        alerts: list[str] = []
        for briefing in reversed(self._briefings):
            for alert in briefing.alerts:
                alerts.append(alert)
                if len(alerts) >= limit:
                    return alerts
        return alerts

    def metrics(self) -> Mapping[str, int]:
        """Return headline metrics for the dashboard."""

        total = len(self._results)
        critical = sum(1 for item in self._results if item.risk_level == "critical")
        high = sum(1 for item in self._results if item.risk_level == "high")
        moderate = sum(1 for item in self._results if item.risk_level == "moderate")
        low = sum(1 for item in self._results if item.risk_level == "low")
        return {
            "total": total,
            "critical": critical,
            "high": high + critical,
            "moderate": moderate,
            "low": low,
        }

    def risk_distribution(self) -> Mapping[str, int]:
        """Return count of analyses by risk level."""

        distribution = {"critical": 0, "high": 0, "moderate": 0, "low": 0}
        for result in self._results:
            if result.risk_level in distribution:
                distribution[result.risk_level] += 1
        return distribution

    def recent_transactions(self, limit: int = 10) -> Sequence[TransactionDigest]:
        """Return latest hops directly related to analysed addresses."""

        records: list[TransactionDigest] = []
        for result in reversed(self._results):
            target = result.address.lower()
            sorted_hops = sorted(result.hops, key=lambda hop: hop.timestamp, reverse=True)
            for hop in sorted_hops:
                direction, counterpart = self._classify_direction(target, hop)
                if direction is None:
                    continue
                records.append(
                    TransactionDigest(
                        analysis_address=result.address,
                        network=result.network,
                        tx_hash=hop.tx_hash or "—",
                        direction=direction,
                        counterpart=counterpart,
                        amount=hop.amount,
                        timestamp=hop.timestamp,
                    )
                )
                if len(records) >= limit:
                    return records
        return records

    def recent_notes(self, limit: int = 10) -> Sequence[str]:
        """Return the latest risk notes from analyses."""

        notes: list[str] = []
        for result in reversed(self._results):
            for note in result.notes:
                notes.append(f"{result.address}: {note}")
                if len(notes) >= limit:
                    return notes
        return notes

    @staticmethod
    def _classify_direction(target: str, hop: TransactionHop) -> tuple[str | None, str]:
        """Determine direction of funds relative to the analysed ``target`` address."""

        from_addr = (hop.from_address or "").lower()
        to_addr = (hop.to_address or "").lower()
        if from_addr == target:
            return "Исходящая", hop.to_address or "—"
        if to_addr == target:
            return "Входящая", hop.from_address or "—"
        return None, ""


__all__ = ["AnalysisStore", "TransactionDigest"]
