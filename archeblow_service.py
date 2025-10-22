"""Core analysis service for the ArcheBlow project.

This module provides high-level orchestration primitives that can be wired
into a desktop UI or CLI tool.  The focus is on a clean, testable
architecture that coordinates multiple open-data providers to classify a
cryptocurrency address.

The implementation below intentionally keeps external integrations abstract.
Concrete clients can be supplied by the application layer depending on which
open APIs are available in the deployment environment.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Iterable, List, Mapping, Protocol, Sequence


class Network(str, Enum):
    """Supported blockchain networks.

    The values correspond to how most public APIs name their networks.
    Additional networks can be added without changing the rest of the code.
    """

    BITCOIN = "bitcoin"
    ETHEREUM = "ethereum"
    LITECOIN = "litecoin"
    POLYGON = "polygon"
    TRON = "tron"


@dataclass(slots=True)
class TransactionHop:
    """Represents a single hop in the flow of funds."""

    tx_hash: str
    from_address: str
    to_address: str
    amount: float
    timestamp: int
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MixerMatch:
    """Describes a detected association with a mixing service."""

    mixer_name: str
    confidence: float
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AddressAnalysisResult:
    """Aggregated result of all checks performed for a wallet address."""

    address: str
    network: Network
    risk_score: float
    risk_level: str
    hops: List[TransactionHop] = field(default_factory=list)
    mixers: List[MixerMatch] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)


class ExplorerClient(Protocol):
    """Protocol for blockchain explorer integrations."""

    network: Network
    service_id: str
    service_name: str

    async def fetch_transaction_hops(self, address: str) -> Sequence[TransactionHop]:
        """Return a chronological list of transaction hops for ``address``."""


class MixerIntelClient(Protocol):
    """Protocol for mixer intelligence sources."""

    service_id: str
    service_name: str

    async def detect_mixers(
        self, address: str, hops: Sequence[TransactionHop]
    ) -> Sequence[MixerMatch]:
        """Return mixer matches associated with the address flow."""


class Heuristic(str, Enum):
    """Simple heuristics used to build an explainable risk score."""

    MIXER_DETECTED = auto()
    FAN_OUT = auto()
    FRESH_FUNDS = auto()
    RAPID_CIRCULATION = auto()


class RiskModel:
    """Combines heuristic scores into a normalized risk indicator."""

    def __init__(self, weights: Mapping[Heuristic, float] | None = None) -> None:
        default_weights = {
            Heuristic.MIXER_DETECTED: 0.6,
            Heuristic.FAN_OUT: 0.15,
            Heuristic.FRESH_FUNDS: 0.1,
            Heuristic.RAPID_CIRCULATION: 0.15,
        }
        self._weights = dict(default_weights)
        if weights:
            self._weights.update(weights)

    def evaluate(
        self,
        *,
        mixers: Sequence[MixerMatch],
        hops: Sequence[TransactionHop],
        notes: List[str],
    ) -> float:
        """Return a score between 0 (clean) and 1 (high risk)."""

        score = 0.0

        if mixers:
            score += self._weights[Heuristic.MIXER_DETECTED]
            highest_confidence = max(match.confidence for match in mixers)
            score += 0.1 * highest_confidence
            notes.append(
                "Обнаружены совпадения с известными миксерами криптовалюты."
            )

        fan_out_ratio = self._estimate_fan_out(hops)
        score += fan_out_ratio * self._weights[Heuristic.FAN_OUT]
        if fan_out_ratio > 0.5:
            notes.append(
                "Высокая степень расщепления средств по множеству адресов."
            )

        if hops and self._is_fresh_funds(hops):
            score += self._weights[Heuristic.FRESH_FUNDS]
            notes.append(
                "Средства поступили на адрес недавно — требуется дополнительная проверка."
            )

        circulation = self._estimate_rapid_circulation(hops)
        score += circulation * self._weights[Heuristic.RAPID_CIRCULATION]
        if circulation > 0.5:
            notes.append(
                "Средства перемещаются по сети с высокой скоростью, что может указывать на попытку сокрытия следов."
            )

        return min(score, 1.0)

    @staticmethod
    def _estimate_fan_out(hops: Sequence[TransactionHop]) -> float:
        """Return a fan-out heuristic between 0 and 1."""

        if not hops:
            return 0.0
        outgoing_map: dict[str, set[str]] = {}
        for hop in hops:
            outgoing_map.setdefault(hop.from_address, set()).add(hop.to_address)
        fan_out_values = [len(destinations) for destinations in outgoing_map.values()]
        if not fan_out_values:
            return 0.0
        max_branches = max(fan_out_values)
        normalized = min(max_branches / 20, 1.0)
        return normalized

    @staticmethod
    def _is_fresh_funds(hops: Sequence[TransactionHop]) -> bool:
        """Detect whether the address received funds within the last day."""

        if not hops:
            return False
        latest_timestamp = max(hop.timestamp for hop in hops)
        earliest_timestamp = min(hop.timestamp for hop in hops)
        return (latest_timestamp - earliest_timestamp) < 86_400

    @staticmethod
    def _estimate_rapid_circulation(hops: Sequence[TransactionHop]) -> float:
        """Return a normalized indicator of rapid fund movement."""

        if len(hops) < 2:
            return 0.0
        sorted_hops = sorted(hops, key=lambda hop: hop.timestamp)
        deltas = [
            sorted_hops[i + 1].timestamp - sorted_hops[i].timestamp
            for i in range(len(sorted_hops) - 1)
        ]
        average_delta = sum(deltas) / len(deltas)
        if average_delta <= 0:
            return 1.0
        normalized = min(1.0, 1.0 / (average_delta / 600))  # 10-minute baseline
        return normalized


class ArcheBlowAnalyzer:
    """Coordinates multiple data sources to produce an address assessment."""

    def __init__(
        self,
        explorer_clients: Iterable[ExplorerClient],
        mixer_clients: Iterable[MixerIntelClient],
        *,
        risk_model: RiskModel | None = None,
    ) -> None:
        self._explorers = list(explorer_clients)
        if not self._explorers:
            raise ValueError("At least one explorer client is required")
        self._mixers = list(mixer_clients)
        self._risk_model = risk_model or RiskModel()

    async def analyze(self, address: str, network: Network) -> AddressAnalysisResult:
        """Perform asynchronous analysis of ``address`` on ``network``."""

        explorer = self._select_explorer(network)
        hops = await explorer.fetch_transaction_hops(address)
        mixers = await self._gather_mixer_matches(address, hops)
        notes: List[str] = []
        risk_score = self._risk_model.evaluate(mixers=mixers, hops=hops, notes=notes)
        risk_level = self._risk_level_from_score(risk_score)
        sources: List[str] = []
        explorer_name = getattr(explorer, "service_name", explorer.__class__.__name__)
        sources.append(explorer_name)
        for mixer_client in self._mixers:
            sources.append(
                getattr(mixer_client, "service_name", mixer_client.__class__.__name__)
            )
        unique_sources = list(dict.fromkeys(sources))
        return AddressAnalysisResult(
            address=address,
            network=network,
            risk_score=risk_score,
            risk_level=risk_level,
            hops=list(hops),
            mixers=list(mixers),
            notes=notes,
            sources=unique_sources,
        )

    def _select_explorer(self, network: Network) -> ExplorerClient:
        for client in self._explorers:
            if client.network == network:
                return client
        raise LookupError(f"No explorer client registered for {network.value}")

    async def _gather_mixer_matches(
        self, address: str, hops: Sequence[TransactionHop]
    ) -> Sequence[MixerMatch]:
        if not self._mixers:
            return []
        tasks = [client.detect_mixers(address, hops) for client in self._mixers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        matches: list[MixerMatch] = []
        for result in results:
            if isinstance(result, Exception):
                continue
            matches.extend(result)
        return matches

    @staticmethod
    def _risk_level_from_score(score: float) -> str:
        if score >= 0.75:
            return "critical"
        if score >= 0.5:
            return "high"
        if score >= 0.25:
            return "moderate"
        return "low"


class InMemoryExplorerClient:
    """Simple explorer that reads hops from a pre-seeded dataset.

    Useful for testing the orchestration logic without calling external APIs.
    """

    def __init__(self, network: Network, hops: Mapping[str, Sequence[TransactionHop]]):
        self.network = network
        self._hops = hops

    async def fetch_transaction_hops(self, address: str) -> Sequence[TransactionHop]:
        await asyncio.sleep(0)
        return list(self._hops.get(address, ()))


class HeuristicMixerClient:
    """Mixer intelligence source based on a static watchlist."""

    def __init__(
        self,
        *,
        watchlist: Mapping[str, str],
        base_confidence: float = 0.7,
        service_id: str = "heuristic_mixer",
        service_name: str = "Heuristic Mixer Watchlist",
    ) -> None:
        self._watchlist = {addr.lower(): name for addr, name in watchlist.items()}
        self._base_confidence = base_confidence
        self.service_id = service_id
        self.service_name = service_name

    async def detect_mixers(
        self, address: str, hops: Sequence[TransactionHop]
    ) -> Sequence[MixerMatch]:
        await asyncio.sleep(0)
        matches: list[MixerMatch] = []
        normalized_address = address.lower()
        if normalized_address in self._watchlist:
            matches.append(
                MixerMatch(
                    mixer_name=self._watchlist[normalized_address],
                    confidence=self._base_confidence,
                    evidence={"match": address},
                )
            )
        for hop in hops:
            candidate = hop.to_address.lower()
            if candidate in self._watchlist:
                matches.append(
                    MixerMatch(
                        mixer_name=self._watchlist[candidate],
                        confidence=self._base_confidence * 0.9,
                        evidence={"tx_hash": hop.tx_hash, "match": hop.to_address},
                    )
                )
        return matches


async def analyze_wallet(
    address: str,
    network: Network,
    *,
    explorer_data: Mapping[str, Sequence[TransactionHop]],
    mixer_watchlist: Mapping[str, str],
) -> AddressAnalysisResult:
    """Convenience helper for quick experiments without wiring dependencies."""

    analyzer = ArcheBlowAnalyzer(
        explorer_clients=[InMemoryExplorerClient(network, explorer_data)],
        mixer_clients=[HeuristicMixerClient(watchlist=mixer_watchlist)],
    )
    return await analyzer.analyze(address, network)


__all__ = [
    "AddressAnalysisResult",
    "ArcheBlowAnalyzer",
    "Heuristic",
    "HeuristicMixerClient",
    "InMemoryExplorerClient",
    "MixerIntelClient",
    "MixerMatch",
    "Network",
    "RiskModel",
    "TransactionHop",
    "analyze_wallet",
]