"""Rule-based artificial analyst that produces actionable briefings."""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as _dt
from typing import Callable, Sequence

from archeblow_service import AddressAnalysisResult, MixerMatch, Network, TransactionHop


@dataclass(slots=True)
class AnalystRecommendation:
    """Represents a concrete follow-up step suggested by the analyst."""

    title: str
    priority: str
    rationale: str
    actions: Sequence[str] = field(default_factory=tuple)


@dataclass(slots=True)
class AnalystBriefing:
    """Structured explanation generated for a completed analysis."""

    address: str
    network: Network
    generated_at: int
    summary: str
    confidence: float
    risk_level: str
    highlights: Sequence[str] = field(default_factory=tuple)
    recommendations: Sequence[AnalystRecommendation] = field(default_factory=tuple)
    alerts: Sequence[str] = field(default_factory=tuple)


class ArtificialAnalyst:
    """Provides explainable insights on top of an ``AddressAnalysisResult``.

    The implementation intentionally stays deterministic: the analyst derives
    its conclusions from the heuristics already collected during the risk
    evaluation step.  This makes the behaviour auditable and keeps the module
    free of external ML dependencies, while still delivering rich guidance for
    operators.
    """

    def __init__(self, *, now_provider: Callable[[], int] | None = None) -> None:
        self._now_provider = now_provider or self._current_utc_timestamp

    @staticmethod
    def _current_utc_timestamp() -> int:
        return int(_dt.datetime.now(_dt.timezone.utc).timestamp())

    def generate_briefing(self, result: AddressAnalysisResult) -> AnalystBriefing:
        """Return a briefing that summarises the supplied ``result``."""

        hops = list(result.hops)
        mixers = list(result.mixers)
        total_volume = sum(abs(hop.amount) for hop in hops if hop.amount)
        counterparties = self._collect_counterparties(result.address, hops)
        last_activity = max((hop.timestamp for hop in hops), default=0)
        age_hours = self._hours_since(last_activity) if last_activity else None
        recent_window = age_hours is not None and age_hours <= 24

        highlights = self._build_highlights(result, mixers, counterparties, total_volume, age_hours)
        recommendations = self._build_recommendations(result, mixers, recent_window, total_volume, counterparties)
        alerts = self._build_alerts(result, mixers, recent_window)
        summary = self._build_summary(result, len(hops), total_volume, recent_window)
        confidence = self._estimate_confidence(len(hops), mixers, recent_window)

        return AnalystBriefing(
            address=result.address,
            network=result.network,
            generated_at=self._now_provider(),
            summary=summary,
            confidence=confidence,
            risk_level=result.risk_level,
            highlights=highlights,
            recommendations=recommendations,
            alerts=alerts,
        )

    def _build_summary(
        self,
        result: AddressAnalysisResult,
        hop_count: int,
        total_volume: float,
        recent_window: bool,
    ) -> str:
        risk_display = {
            "critical": "критический",
            "high": "высокий",
            "moderate": "средний",
            "low": "низкий",
        }.get(result.risk_level, "неопределенный")
        summary_parts = [
            f"Анализ адреса {result.address} в сети {result.network.name.upper()} завершен",
            f"уровень риска оценивается как {risk_display} ({result.risk_score:.2f})",
        ]
        if hop_count:
            summary_parts.append(f"проанализировано транзакций: {hop_count}")
        if total_volume:
            summary_parts.append(f"совокупный оборот: {total_volume:.4f}")
        if recent_window:
            summary_parts.append("обнаружена свежая активность (последние 24 часа)")
        return ", ".join(summary_parts) + "."

    def _build_highlights(
        self,
        result: AddressAnalysisResult,
        mixers: Sequence[MixerMatch],
        counterparties: Sequence[str],
        total_volume: float,
        age_hours: float | None,
    ) -> list[str]:
        highlights: list[str] = []
        if mixers:
            mixer_names = {match.mixer_name for match in mixers}
            highlights.append(
                "Обнаружены совпадения с миксерами: " + ", ".join(sorted(mixer_names))
            )
        if result.notes:
            highlights.extend(result.notes)
        if total_volume:
            highlights.append(f"Оценочный оборот по кошельку: {total_volume:.4f}")
        if len(counterparties) >= 10:
            highlights.append(
                f"Высокая сетевое взаимодействие — обнаружено {len(counterparties)} уникальных контрагентов"
            )
        if age_hours is not None:
            highlights.append(f"Последняя активность {age_hours:.1f} часов назад")
        return highlights

    def _build_recommendations(
        self,
        result: AddressAnalysisResult,
        mixers: Sequence[MixerMatch],
        recent_window: bool,
        total_volume: float,
        counterparties: Sequence[str],
    ) -> list[AnalystRecommendation]:
        recs: list[AnalystRecommendation] = []
        if result.risk_level in {"critical", "high"}:
            recs.append(
                AnalystRecommendation(
                    title="Немедленные меры контроля",
                    priority="Высокий",
                    rationale="Повышенный риск выявлен основным анализом.",
                    actions=[
                        "Заблокировать связанные операции до завершения ручной проверки",
                        "Создать инцидент в системе мониторинга комплаенса",
                    ],
                )
            )
        elif result.risk_level == "moderate":
            recs.append(
                AnalystRecommendation(
                    title="Расширенный мониторинг",
                    priority="Средний",
                    rationale="Риск умеренный — требуется периодический пересмотр.",
                    actions=[
                        "Добавить адрес в список наблюдения на 30 дней",
                        "Собрать дополнительные метаданные по контрагентам",
                    ],
                )
            )
        else:
            recs.append(
                AnalystRecommendation(
                    title="Регламентная проверка",
                    priority="Низкий",
                    rationale="Признаков повышенного риска не выявлено.",
                    actions=[
                        "Зафиксировать результат и продолжить стандартный мониторинг",
                        "Актуализировать данные профиля клиента",
                    ],
                )
            )

        if mixers:
            recs.append(
                AnalystRecommendation(
                    title="Проверка подозрительных сервисов",
                    priority="Высокий",
                    rationale="Система обнаружила совпадения с миксерами.",
                    actions=[
                        "Запросить дополнительные доказательства происхождения средств",
                        "Передать кейс в группу по расследованиям",
                    ],
                )
            )

        if recent_window:
            recs.append(
                AnalystRecommendation(
                    title="Мониторинг свежих поступлений",
                    priority="Средний",
                    rationale="В течение последних 24 часов отмечена активность.",
                    actions=[
                        "Настроить оповещение при поступлении новых транзакций",
                        "Сверить поступления с легитимными источниками",
                    ],
                )
            )

        if total_volume >= 10:
            recs.append(
                AnalystRecommendation(
                    title="Финансовый аудит",
                    priority="Высокий",
                    rationale="Кошелек показал оборот более 10 единиц валюты.",
                    actions=[
                        "Собрать информацию о происхождении крупных сумм",
                        "Сверить операции с внутренними лимитами",
                    ],
                )
            )

        if len(counterparties) >= 15:
            recs.append(
                AnalystRecommendation(
                    title="Анализ контрагентов",
                    priority="Средний",
                    rationale="Выявлено большое число уникальных получателей/отправителей.",
                    actions=[
                        "Кластеризовать адреса и выделить связанные группы",
                        "Проверить пересечения с санкционными списками",
                    ],
                )
            )
        return recs

    def _build_alerts(
        self,
        result: AddressAnalysisResult,
        mixers: Sequence[MixerMatch],
        recent_window: bool,
    ) -> list[str]:
        alerts: list[str] = []
        if result.risk_level in {"critical", "high"}:
            alerts.append(
                f"{result.address}: требуется немедленная реакция из-за высокого уровня риска"
            )
        if mixers:
            alerts.append(f"{result.address}: обнаружены совпадения с миксерами")
        if recent_window:
            alerts.append(f"{result.address}: зафиксирована свежая активность, рекомендуется мониторинг")
        return alerts

    def _estimate_confidence(
        self,
        hop_count: int,
        mixers: Sequence[MixerMatch],
        recent_window: bool,
    ) -> float:
        base = 0.4 if hop_count >= 3 else 0.25
        coverage = min(0.45, hop_count * 0.02)
        mixer_bonus = 0.1 if mixers else 0.0
        recency_bonus = 0.05 if recent_window else 0.0
        return min(1.0, base + coverage + mixer_bonus + recency_bonus)

    def _collect_counterparties(
        self, address: str, hops: Sequence[TransactionHop]
    ) -> list[str]:
        normalized = address.lower()
        parties: set[str] = set()
        for hop in hops:
            if hop.from_address and hop.from_address.lower() != normalized:
                parties.add(hop.from_address)
            if hop.to_address and hop.to_address.lower() != normalized:
                parties.add(hop.to_address)
        return sorted(parties)

    def _hours_since(self, timestamp: int) -> float:
        now = self._now_provider()
        delta = max(0, now - timestamp)
        return delta / 3600


def analyst_playbook() -> str:
    """Return a human-readable description of the analyst workflow."""

    return (
        "Искусственный аналитик ArcheBlow автоматически интерпретирует результаты "
        "проверки адреса. Он оценивает текущий уровень риска, выделяет ключевые "
        "факты (мексеры, активность, объем операций) и формирует набор "
        "рекомендаций: от срочных мер до планового мониторинга. Используйте его "
        "выводы как отправную точку для ручного расследования, фиксируйте "
        "рекомендованные действия и обновляйте статусы инцидентов в ваших "
        "внутренних системах."
    )


__all__ = [
    "AnalystBriefing",
    "AnalystRecommendation",
    "ArtificialAnalyst",
    "analyst_playbook",
]
