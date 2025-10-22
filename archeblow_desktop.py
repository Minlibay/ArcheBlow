"""ArcheBlow desktop interface prototype.

This module implements a PySide6-based desktop UI that follows the
information architecture outlined in ``ux-design.md``.  The goal of this
prototype is to provide a launchable application that demonstrates the
navigation, screens, and core interactions expected from the final client.

The UI focuses on presenting a rich layout with reusable components and
placeholder widgets for data visualisations, forms, and logs.  It is wired to
an asynchronous event loop via ``qasync`` so that future integrations with the
``archeblow_service`` orchestration layer can run without blocking the GUI.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
import datetime as _dt
import math
from typing import Iterable, Mapping, Sequence

from PySide6 import QtCore, QtGui, QtWidgets
from qasync import QEventLoop, asyncSlot

from archeblow_service import (
    AddressAnalysisResult,
    ArcheBlowAnalyzer,
    HeuristicMixerClient,
    Network,
)
from analysis_store import AnalysisStore
from api_keys import API_SERVICE_KEYS, get_api_key, get_masked_key
from ai_analyst import AnalystBriefing, ArtificialAnalyst, analyst_playbook
from explorers import (
    ExplorerAPIError,
    SUPPORTED_NETWORKS,
    UnsupportedNetworkError,
    create_explorer_clients,
)
from monitoring import MonitoringService
def _current_utc_timestamp() -> int:
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


_RISK_BADGE = {
    "critical": ("Критический", "Высокий"),
    "high": ("Высокий", "Высокий"),
    "moderate": ("Средний", "Средний"),
    "low": ("Низкий", "Низкий"),
}


def _risk_to_display(level: str) -> tuple[str, str]:
    return _RISK_BADGE.get(level, ("Неизвестно", "Низкий"))


def _short_address(value: str) -> str:
    if len(value) <= 15:
        return value
    return f"{value[:6]}…{value[-4:]}"


def _service_display_name(service_id: str) -> str:
    entry = API_SERVICE_KEYS.get(service_id)
    if entry:
        return entry.display_name
    return service_id


_DEFAULT_MIXER_WATCHLIST: Mapping[str, str] = {
    "1Jz2Jv7wYyh9wA8Ski38p8h9Cwz9zmXo4H": "ChipMixer (public sample)",
    "bc1qwasab1example0000000000000000v2a8d0": "Wasabi Wallet Cluster",
    "3JZq4atUahhuA9rLhXLMhhTo133J9rF97j": "Bitcoin Fog (historic)",
}


@dataclass(frozen=True)
class NavItem:
    """Describes an item displayed in the left navigation panel."""

    title: str
    page_id: str
    icon: str


class NavigationButton(QtWidgets.QPushButton):
    """Flat navigation button with icon and hover state."""

    def __init__(self, item: NavItem, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(item.title, parent)
        self.item = item
        self.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.setCheckable(True)
        self.setIcon(self._create_icon(item.icon))
        self.setIconSize(QtCore.QSize(18, 18))
        self.setMinimumHeight(40)
        self.setStyleSheet(
            """
            QPushButton {
                color: #c9d1d9;
                background: transparent;
                border-radius: 6px;
                padding: 8px 12px;
                text-align: left;
            }
            QPushButton:hover {
                background: rgba(56, 139, 253, 0.2);
            }
            QPushButton:checked {
                background: rgba(56, 139, 253, 0.3);
                color: #ffffff;
            }
            """
        )

    @staticmethod
    def _create_icon(name: str) -> QtGui.QIcon:
        # Placeholder Feather-like icons created from emoji glyphs to avoid
        # bundling assets.
        pixmap = QtGui.QPixmap(32, 32)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setFont(QtGui.QFont("Segoe UI Emoji", 18))
        painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, name)
        painter.end()
        return QtGui.QIcon(pixmap)


class NavigationPanel(QtWidgets.QFrame):
    """Container that holds the brand header and navigation actions."""

    selection_changed = QtCore.Signal(str)

    def __init__(self, nav_items: Iterable[NavItem], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, NavigationButton] = {}
        self.setObjectName("navigationPanel")
        self.setFixedWidth(220)
        self.setStyleSheet(
            "#navigationPanel { background-color: #0d1117; border-right: 1px solid #30363d; }"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 24, 16, 16)
        layout.setSpacing(12)

        header = QtWidgets.QLabel("ArcheBlow")
        header.setStyleSheet("color: #58a6ff; font-size: 20px; font-weight: 700;")
        layout.addWidget(header)

        subtitle = QtWidgets.QLabel("Open Compliance Intelligence")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(subtitle)

        layout.addSpacing(20)

        for item in nav_items:
            button = NavigationButton(item)
            button.clicked.connect(self._handle_click)
            layout.addWidget(button)
            self._buttons[item.page_id] = button

        layout.addStretch(1)

    def _handle_click(self) -> None:
        button = self.sender()
        if not isinstance(button, NavigationButton):
            return
        for other in self._buttons.values():
            if other is not button:
                other.setChecked(False)
        button.setChecked(True)
        self.selection_changed.emit(button.item.page_id)

    def set_active(self, page_id: str) -> None:
        if page_id in self._buttons:
            self._buttons[page_id].setChecked(True)
            for pid, btn in self._buttons.items():
                if pid != page_id:
                    btn.setChecked(False)


class SearchField(QtWidgets.QWidget):
    """Global search bar with keyboard shortcut hint."""

    request_search = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("Поиск адресов, тегов или отчетов… (Ctrl+K)")
        self.input.setStyleSheet(
            """
            QLineEdit {
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 8px;
                color: #c9d1d9;
                padding: 8px 12px;
            }
            QLineEdit:focus {
                border-color: #58a6ff;
            }
            """
        )
        self.input.returnPressed.connect(self._emit_search)
        layout.addWidget(self.input)

        hint = QtWidgets.QLabel("Ctrl/Cmd + K")
        hint.setStyleSheet("color: #8b949e; font-size: 11px; background: #0d1117; padding: 0 6px;")
        layout.addWidget(hint)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() == QtCore.Qt.Key_K and event.modifiers() in (
            QtCore.Qt.ControlModifier,
            QtCore.Qt.MetaModifier,
        ):
            self.input.setFocus()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _emit_search(self) -> None:
        self.request_search.emit(self.input.text())


class StatusIndicator(QtWidgets.QFrame):
    """Displays live statistics derived from completed analyses."""

    def __init__(
        self,
        store: AnalysisStore | None = None,
        monitoring: MonitoringService | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._monitoring = monitoring
        self.setStyleSheet("color: #8b949e;")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(16)

        sync_icon = QtWidgets.QLabel("🔄")
        layout.addWidget(sync_icon)
        self.sync_label = QtWidgets.QLabel()
        layout.addWidget(self.sync_label)

        tasks_icon = QtWidgets.QLabel("📊")
        layout.addWidget(tasks_icon)
        self.task_label = QtWidgets.QLabel()
        layout.addWidget(self.task_label)

        monitoring_icon = QtWidgets.QLabel("🛰️")
        layout.addWidget(monitoring_icon)
        self.monitoring_label = QtWidgets.QLabel()
        layout.addWidget(self.monitoring_label)

        layout.addStretch(1)

        self._refresh_metrics()
        self._refresh_monitoring()
        if self._store is not None:
            self._store.result_added.connect(self._on_result_added)
        if self._monitoring is not None:
            self._monitoring.event_recorded.connect(self._on_monitoring_event)
            self._monitoring.watch_added.connect(self._on_monitoring_event)

    def _refresh_metrics(self) -> None:
        if self._store is None:
            self.sync_label.setText("Анализы еще не запускались")
            self.task_label.setText("Нет данных о рисках")
            return
        metrics = self._store.metrics()
        distribution = self._store.risk_distribution()
        total = metrics.get("total", 0)
        critical = distribution.get("critical", 0)
        high = distribution.get("high", 0)
        moderate = distribution.get("moderate", 0)
        low = distribution.get("low", 0)
        self.sync_label.setText(f"Анализов выполнено: {total}")
        self.task_label.setText(
            " | ".join(
                [
                    f"Критический риск: {critical}",
                    f"Высокий риск: {high}",
                    f"Средний риск: {moderate}",
                    f"Низкий риск: {low}",
                ]
            )
        )

    def _refresh_monitoring(self) -> None:
        if self._monitoring is None:
            self.monitoring_label.setText("Мониторинг не активирован")
            return
        watches = len(self._monitoring.active_watches())
        incidents = self._monitoring.active_api_incidents()
        if incidents:
            incident_parts = [
                f"{item.get('service_name', item.get('service_id'))}: {item.get('failures', 0)}"
                for item in incidents
            ]
            incident_text = ", ".join(incident_parts)
        else:
            incident_text = "API стабильны"
        self.monitoring_label.setText(
            f"Мониторинг: {watches} адрес(ов); {incident_text}"
        )

    def _on_result_added(self, _result: AddressAnalysisResult) -> None:
        self._refresh_metrics()

    def _on_monitoring_event(self, _event: object) -> None:
        self._refresh_monitoring()


class NotificationCenter(QtWidgets.QFrame):
    """Notification icon that surfaces recent risk notes and monitoring alerts."""

    def __init__(
        self,
        store: AnalysisStore | None = None,
        monitoring: MonitoringService | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._monitoring = monitoring
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.button = QtWidgets.QPushButton("🔔")
        self.button.setFlat(True)
        self.button.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.button.setStyleSheet(
            """
            QPushButton {
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 10px;
                padding: 6px 10px;
            }
            QPushButton:hover { background: rgba(56, 139, 253, 0.2); }
            """
        )
        self.button.clicked.connect(self._show_notifications)
        layout.addWidget(self.button)

        self.counter = QtWidgets.QLabel()
        self.counter.setStyleSheet(
            "color: #ffffff; background: #d29922; padding: 2px 6px; border-radius: 8px;"
        )
        layout.addWidget(self.counter)

        self._update_counter()
        if self._store is not None:
            self._store.result_added.connect(self._handle_result_added)
        if self._monitoring is not None:
            self._monitoring.event_recorded.connect(self._on_monitoring_event)
            self._monitoring.watch_added.connect(self._on_monitoring_event)

    def _recent_notes(self) -> Sequence[str]:
        if self._store is None:
            return []
        notes = list(self._store.recent_notes(limit=5))
        alerts = list(self._store.analyst_alerts(limit=5))
        combined: list[str] = []
        if self._monitoring is not None:
            for event in self._monitoring.recent_events(limit=5):
                if event.level not in {"error", "warning"}:
                    continue
                combined.append(self._format_monitoring_event(event))
                if len(combined) >= 5:
                    return combined
        combined.extend(alerts)
        for note in notes:
            if len(combined) >= 5:
                break
            combined.append(note)
        return combined

    def _update_counter(self) -> None:
        notes = self._recent_notes()
        if not notes:
            self.counter.hide()
            return
        self.counter.setText(str(len(notes)))
        self.counter.show()

    def _handle_result_added(self, _result: AddressAnalysisResult) -> None:
        self._update_counter()

    def _on_monitoring_event(self, _event: object) -> None:
        self._update_counter()

    def _show_notifications(self) -> None:
        menu = QtWidgets.QMenu(self)
        notes = self._recent_notes()
        if not notes:
            action = menu.addAction("Новых уведомлений нет")
            action.setEnabled(False)
        else:
            for note in notes:
                action = menu.addAction(note)
                action.setEnabled(False)
        menu.exec(self.button.mapToGlobal(QtCore.QPoint(0, self.button.height())))

    @staticmethod
    def _format_monitoring_event(event: object) -> str:
        if not isinstance(event, dict) and not hasattr(event, "details"):
            return str(event)
        if hasattr(event, "details"):
            details = getattr(event, "details", {})
            level = getattr(event, "level", "info")
            timestamp = getattr(event, "timestamp", _current_utc_timestamp())
            message = getattr(event, "message", "")
        else:
            details = event.get("details", {})
            level = event.get("level", "info")
            timestamp = event.get("timestamp", _current_utc_timestamp())
            message = event.get("message", "")
        service_name = details.get("service_name") or _service_display_name(
            details.get("service_id", "")
        )
        ts_text = (
            QtCore.QDateTime.fromSecsSinceEpoch(timestamp, QtCore.QTimeZone.utc())
            .toLocalTime()
            .toString("HH:mm")
        )
        level_display = level.upper()
        return f"{ts_text} • [{level_display}] {service_name}: {message}"


class TopBar(QtWidgets.QFrame):
    """Combines search, status indicators and notifications."""

    request_search = QtCore.Signal(str)

    def __init__(
        self,
        store: AnalysisStore | None = None,
        monitoring: MonitoringService | None = None,
    ) -> None:
        super().__init__()
        self.setStyleSheet("background: #0d1117; border-bottom: 1px solid #30363d;")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(16)

        self.search = SearchField()
        self.search.request_search.connect(self.request_search)
        layout.addWidget(self.search, 3)

        self.status = StatusIndicator(store, monitoring)
        layout.addWidget(self.status, 2)

        self.notifications = NotificationCenter(store, monitoring)
        layout.addWidget(self.notifications, 1)


class RiskDistributionWidget(QtWidgets.QWidget):
    """Displays a simple distribution of risk levels using progress bars."""

    _ORDER: tuple[tuple[str, str, str], ...] = (
        ("critical", "Критический", "#f85149"),
        ("high", "Высокий", "#d29922"),
        ("moderate", "Средний", "#bf7fff"),
        ("low", "Низкий", "#2ea043"),
    )

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._bars: dict[str, QtWidgets.QProgressBar] = {}
        for key, label, color in self._ORDER:
            bar = QtWidgets.QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat(f"{label}: 0 (0%)")
            bar.setStyleSheet(
                """
                QProgressBar {
                    background: #0d1117;
                    border: 1px solid #30363d;
                    border-radius: 8px;
                    text-align: center;
                    color: #c9d1d9;
                }
                QProgressBar::chunk {
                    border-radius: 6px;
                }
                """
            )
            bar.setProperty("chunk_color", color)
            layout.addWidget(bar)
            self._bars[key] = bar

    def update_distribution(self, distribution: Mapping[str, int]) -> None:
        total = sum(distribution.values()) or 1
        for key, label, color in self._ORDER:
            bar = self._bars[key]
            count = distribution.get(key, 0)
            percent = int(round((count / total) * 100))
            # Apply chunk color dynamically.
            bar.setStyleSheet(
                """
                QProgressBar {
                    background: #0d1117;
                    border: 1px solid #30363d;
                    border-radius: 8px;
                    text-align: center;
                    color: #c9d1d9;
                }
                QProgressBar::chunk {
                    background-color: %s;
                    border-radius: 6px;
                }
                """
                % color
            )
            bar.setValue(percent)
            bar.setFormat(f"{label}: {count} ({percent}%)")


class DashboardPage(QtWidgets.QWidget):
    """Dashboard showing live metrics based on completed analyses."""

    def __init__(
        self,
        store: AnalysisStore,
        monitoring: MonitoringService | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._monitoring = monitoring

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        cards = QtWidgets.QGridLayout()
        cards.setHorizontalSpacing(16)
        cards.setVerticalSpacing(16)

        self._metric_labels: dict[str, QtWidgets.QLabel] = {}
        card_specs = [
            ("Всего анализов", "total", "📊"),
            ("Высокий риск", "high", "🛑"),
            ("Средний риск", "moderate", "⚠️"),
            ("Низкий риск", "low", "✅"),
        ]
        for column, (title, key, icon) in enumerate(card_specs):
            card, value_label = self._metric_card(title, icon)
            cards.addWidget(card, 0, column)
            self._metric_labels[key] = value_label
        layout.addLayout(cards)

        distribution = QtWidgets.QGroupBox("Распределение индекса риска")
        distribution.setLayout(QtWidgets.QVBoxLayout())
        self.risk_distribution = RiskDistributionWidget()
        distribution.layout().addWidget(self.risk_distribution)
        layout.addWidget(distribution)

        transactions = QtWidgets.QGroupBox("Последние транзакции")
        tx_layout = QtWidgets.QVBoxLayout()
        self.tx_table = QtWidgets.QTableWidget(0, 6)
        self.tx_table.setHorizontalHeaderLabels(
            [
                "TX Hash",
                "Адрес",
                "Контрагент",
                "Сумма (BTC)",
                "Направление",
                "Время",
            ]
        )
        self.tx_table.horizontalHeader().setStretchLastSection(True)
        tx_layout.addWidget(self.tx_table)
        transactions.setLayout(tx_layout)
        layout.addWidget(transactions)

        notifications = QtWidgets.QGroupBox("Центр уведомлений")
        notif_layout = QtWidgets.QVBoxLayout()
        self.notifications_list = QtWidgets.QListWidget()
        notif_layout.addWidget(self.notifications_list)
        notifications.setLayout(notif_layout)
        layout.addWidget(notifications)

        monitoring_box = QtWidgets.QGroupBox("Система мониторинга")
        monitoring_layout = QtWidgets.QVBoxLayout()
        self.monitoring_watch_table = QtWidgets.QTableWidget(0, 4)
        self.monitoring_watch_table.setHorizontalHeaderLabels(
            ["Адрес", "Сеть", "Истекает", "Комментарий"]
        )
        self.monitoring_watch_table.horizontalHeader().setStretchLastSection(True)
        monitoring_layout.addWidget(self.monitoring_watch_table)
        self.api_status_list = QtWidgets.QListWidget()
        self.api_status_list.setAlternatingRowColors(True)
        monitoring_layout.addWidget(self.api_status_list)
        monitoring_box.setLayout(monitoring_layout)
        layout.addWidget(monitoring_box)

        analyst_box = QtWidgets.QGroupBox("Рекомендации искусственного аналитика")
        analyst_layout = QtWidgets.QVBoxLayout()
        self.ai_recommendations_list = QtWidgets.QListWidget()
        analyst_layout.addWidget(self.ai_recommendations_list)
        analyst_box.setLayout(analyst_layout)
        layout.addWidget(analyst_box)

        self._store.result_added.connect(self._refresh)
        if self._monitoring is not None:
            self._monitoring.event_recorded.connect(self._on_monitoring_event)
            self._monitoring.watch_added.connect(self._on_monitoring_event)
        self._refresh()

    def _refresh(self) -> None:
        metrics = self._store.metrics()
        for key, label in self._metric_labels.items():
            label.setText(str(metrics.get(key, 0)))
        self.risk_distribution.update_distribution(self._store.risk_distribution())
        self._refresh_transactions()
        self._refresh_notifications()
        self._refresh_monitoring()
        self._refresh_ai_recommendations()

    def _refresh_transactions(self) -> None:
        records = self._store.recent_transactions(limit=10)
        self.tx_table.setRowCount(len(records))
        for row, record in enumerate(records):
            tx_hash_raw = record.tx_hash or "—"
            tx_hash = tx_hash_raw if len(tx_hash_raw) <= 16 else f"{tx_hash_raw[:12]}…"
            analysis_addr = f"{_short_address(record.analysis_address)} ({record.network.name.upper()})"
            counterpart = _short_address(record.counterpart)
            amount = f"{record.amount:.8f}".rstrip("0").rstrip(".") if record.amount else "0"
            timestamp = (
                QtCore.QDateTime.fromSecsSinceEpoch(record.timestamp, QtCore.QTimeZone.utc())
                .toLocalTime()
                .toString("dd.MM.yyyy HH:mm")
            )
            values = [tx_hash, analysis_addr, counterpart, amount, record.direction, timestamp]
            for column, value in enumerate(values):
                self.tx_table.setItem(row, column, QtWidgets.QTableWidgetItem(value))

    def _refresh_notifications(self) -> None:
        notes = list(self._store.recent_notes(limit=5))
        alerts = list(self._store.analyst_alerts(limit=5))
        combined: list[str] = []
        if self._monitoring is not None:
            for event in self._monitoring.recent_events(limit=5):
                if event.level not in {"error", "warning"}:
                    continue
                combined.append(NotificationCenter._format_monitoring_event(event))
                if len(combined) >= 5:
                    break
        combined.extend(alerts)
        for note in notes:
            if len(combined) >= 5:
                break
            combined.append(note)

        self.notifications_list.clear()
        if not combined:
            self.notifications_list.addItem("Пока нет комментариев по рискам.")
            return
        self.notifications_list.addItems(combined)

    def _refresh_monitoring(self) -> None:
        if self._monitoring is None:
            self.monitoring_watch_table.setRowCount(0)
            self.api_status_list.clear()
            self.api_status_list.addItem("Мониторинг не активирован.")
            return

        watches = self._monitoring.active_watches()
        self.monitoring_watch_table.setRowCount(len(watches))
        for row, watch in enumerate(watches):
            expiry = (
                QtCore.QDateTime.fromSecsSinceEpoch(watch.expires_at, QtCore.QTimeZone.utc())
                .toLocalTime()
                .toString("dd.MM.yyyy HH:mm")
            )
            values = [
                watch.address,
                watch.network.name.upper(),
                expiry,
                watch.comment or "—",
            ]
            for column, value in enumerate(values):
                self.monitoring_watch_table.setItem(
                    row, column, QtWidgets.QTableWidgetItem(value)
                )
        if not watches:
            self.monitoring_watch_table.setRowCount(0)

        self.api_status_list.clear()
        statuses = self._monitoring.api_status_snapshot()
        if not statuses:
            self.api_status_list.addItem("API ошибок не обнаружено.")
            return

        def _format_ts(raw: object) -> str:
            if not raw:
                return "—"
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return "—"
            return (
                QtCore.QDateTime.fromSecsSinceEpoch(value, QtCore.QTimeZone.utc())
                .toLocalTime()
                .toString("dd.MM HH:mm")
            )

        for state in statuses:
            service_name = state.get("service_name") or state.get("service_id")
            status = state.get("status", "ok")
            failures = int(state.get("failures", 0) or 0)
            if status == "error":
                detail = (
                    f"ошибки: {failures}, последнее: {_format_ts(state.get('last_error'))}"
                )
                prefix = "⚠️"
            else:
                detail = f"успех: {_format_ts(state.get('last_success'))}"
                prefix = "✅"
            message = state.get("last_error_message") if status == "error" else state.get("last_message")
            if message:
                detail = f"{detail} — {message}"
            self.api_status_list.addItem(f"{prefix} {service_name}: {detail}")

    def _on_monitoring_event(self, _event: object) -> None:
        self._refresh_monitoring()

    def _refresh_ai_recommendations(self) -> None:
        briefings = self._store.recent_briefings(limit=5)
        self.ai_recommendations_list.clear()
        if not briefings:
            self.ai_recommendations_list.addItem(
                "Рекомендации появятся после выполнения анализов."
            )
            return

        for briefing in briefings:
            if briefing.recommendations:
                primary = briefing.recommendations[0]
                text = (
                    f"{briefing.address} ({briefing.network.name.upper()}): "
                    f"{primary.title} — приоритет {primary.priority}"
                )
            else:
                text = (
                    f"{briefing.address} ({briefing.network.name.upper()}): {briefing.summary}"
                )
            self.ai_recommendations_list.addItem(text)

    def _metric_card(
        self, title: str, icon: str
    ) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel]:
        card = QtWidgets.QFrame()
        card.setStyleSheet(
            "background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 16px;"
        )
        layout = QtWidgets.QVBoxLayout(card)
        layout.addWidget(QtWidgets.QLabel(icon), alignment=QtCore.Qt.AlignRight)
        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet("color: #8b949e; font-size: 12px;")
        layout.addWidget(title_label)
        value_label = QtWidgets.QLabel("0")
        value_label.setStyleSheet("color: #ffffff; font-size: 24px; font-weight: 600;")
        layout.addWidget(value_label)
        return card, value_label


class NewAnalysisPage(QtWidgets.QWidget):
    """Form to launch new address analysis tasks."""

    analysis_completed = QtCore.Signal(AddressAnalysisResult)

    def __init__(self, monitoring: MonitoringService | None = None) -> None:
        super().__init__()
        self._monitoring = monitoring
        self._active_explorer_id: str | None = None
        self._active_address: str | None = None
        self._active_network: Network | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        title = QtWidgets.QLabel("Новый анализ")
        title.setStyleSheet("color: #ffffff; font-size: 24px; font-weight: 600;")
        layout.addWidget(title)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(16)

        self.address_input = QtWidgets.QLineEdit()
        self.address_input.setPlaceholderText("Введите адрес или кошелек…")
        form.addRow("Адрес/кошелек", self.address_input)

        self.network_combo = QtWidgets.QComboBox()
        for network in SUPPORTED_NETWORKS:
            self.network_combo.addItem(network.name.title(), network)
        if self.network_combo.count() == 0:
            self.network_combo.addItem("Нет доступных сетей", None)
            self.network_combo.setEnabled(False)
        form.addRow("Блокчейн-сеть", self.network_combo)

        depth_group = QtWidgets.QGroupBox("Глубина анализа")
        depth_layout = QtWidgets.QVBoxLayout()
        self.depth_1 = QtWidgets.QCheckBox("1 хоп")
        self.depth_2 = QtWidgets.QCheckBox("2 хопа")
        self.depth_3 = QtWidgets.QCheckBox("3+ хопов")
        depth_layout.addWidget(self.depth_1)
        depth_layout.addWidget(self.depth_2)
        depth_layout.addWidget(self.depth_3)
        depth_group.setLayout(depth_layout)
        form.addRow(depth_group)

        self.monitoring_toggle = QtWidgets.QCheckBox("Включить периодический мониторинг")
        form.addRow("Мониторинг", self.monitoring_toggle)

        self.notes_input = QtWidgets.QPlainTextEdit()
        self.notes_input.setPlaceholderText("Комментарии и теги…")
        self.notes_input.setFixedHeight(80)
        form.addRow("Комментарии", self.notes_input)

        layout.addLayout(form)

        self.launch_button = QtWidgets.QPushButton("Запустить анализ")
        self.launch_button.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.launch_button.setStyleSheet(
            """
            QPushButton {
                background: #238636;
                color: #ffffff;
                border-radius: 10px;
                padding: 12px 28px;
                font-size: 16px;
            }
            QPushButton:hover { background: #2ea043; }
            QPushButton:disabled { background: #30363d; color: #8b949e; }
            """
        )
        self.launch_button.clicked.connect(self._handle_launch)
        layout.addWidget(self.launch_button, alignment=QtCore.Qt.AlignRight)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.log_output = QtWidgets.QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet(
            "background: #0d1117; border: 1px solid #30363d; border-radius: 12px; color: #8b949e;"
        )
        self.log_output.setPlaceholderText("Логи выполнения появятся здесь…")
        layout.addWidget(self.log_output)

    @asyncSlot()
    async def _handle_launch(self) -> None:
        address = self.address_input.text().strip()
        if not address:
            QtWidgets.QMessageBox.warning(self, "Адрес не указан", "Введите адрес для анализа.")
            return

        network = self._resolve_selected_network()
        if network is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Сеть не выбрана",
                "Выберите поддерживаемую сеть для анализа.",
            )
            self.log_output.append("Выберите поддерживаемую сеть для анализа.")
            return

        self._active_address = address
        self._active_network = network
        self._active_explorer_id = None

        self.launch_button.setDisabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress.setValue(0)
        self.log_output.clear()
        self.log_output.append(
            f"Старт анализа адреса {address} в сети {network.name.title()}…"
        )
        self.log_output.append("Подключение к публичным API выбранной сети…")

        result: AddressAnalysisResult | None = None
        try:
            result = await self._perform_analysis(address, network)
        except UnsupportedNetworkError as exc:
            self._handle_error(str(exc))
            QtWidgets.QMessageBox.warning(self, "Сеть не поддерживается", str(exc))
            return
        except ExplorerAPIError as exc:
            self._handle_error(str(exc))
            QtWidgets.QMessageBox.warning(self, "Ошибка API", str(exc))
            return
        except Exception as exc:
            self._handle_error(f"Ошибка анализа: {exc}")
            QtWidgets.QMessageBox.critical(
                self,
                "Не удалось выполнить анализ",
                f"Произошла ошибка при обращении к публичному API: {exc}",
            )
            return
        finally:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.progress.setVisible(False)
            self.launch_button.setEnabled(True)

        if result is None:
            return

        self.log_output.append("Анализ завершен. Результаты доступны на вкладке 'Анализы'.")
        self.log_output.append("Формирование заключения искусственного аналитика…")

        if self._monitoring is not None:
            self._monitoring.log(
                "info",
                f"Анализ адреса {address} ({network.name.upper()}) завершен успешно.",
                source="analysis_ui",
                category="analysis",
                details={
                    "address": address,
                    "network": network.value,
                    "service_name": "Форма нового анализа",
                },
            )

        if self.monitoring_toggle.isChecked() and self._monitoring is not None:
            watch = self._monitoring.schedule_watch(
                address,
                network,
                days=30,
                comment="Запущено пользователем из формы нового анализа",
            )
            expiry_text = (
                QtCore.QDateTime.fromSecsSinceEpoch(watch.expires_at, QtCore.QTimeZone.utc())
                .toLocalTime()
                .toString("dd.MM.yyyy HH:mm")
            )
            self.log_output.append(
                f"Адрес добавлен в мониторинг до {expiry_text}."
            )

        self.analysis_completed.emit(result)

    def _resolve_selected_network(self) -> Network | None:
        data = self.network_combo.currentData()
        if isinstance(data, Network):
            return data
        if isinstance(data, str) and data:
            try:
                return Network(data)
            except ValueError:
                try:
                    return Network(data.lower())
                except ValueError:
                    return None
        text = self.network_combo.currentText().strip().lower()
        if not text:
            return None
        try:
            return Network(text)
        except ValueError:
            return None

    async def _perform_analysis(self, address: str, network: Network) -> AddressAnalysisResult:
        self.log_output.append("Запрос истории транзакций…")
        explorers = list(create_explorer_clients(network))
        primary_client = explorers[0] if explorers else None
        if primary_client is not None:
            self._active_explorer_id = getattr(primary_client, "service_id", None)
        for client in explorers:
            friendly_name = getattr(
                client,
                "service_name",
                client.__class__.__name__.replace("ExplorerClient", " API"),
            )
            self.log_output.append(f"Используется {friendly_name} для получения данных…")
        mixer_client = HeuristicMixerClient(watchlist=_DEFAULT_MIXER_WATCHLIST)
        analyzer = ArcheBlowAnalyzer(
            explorer_clients=list(explorers),
            mixer_clients=[mixer_client],
        )
        try:
            result = await analyzer.analyze(address, network)
        except ExplorerAPIError as exc:
            if self._monitoring is not None and primary_client is not None:
                self._monitoring.record_api_error(
                    primary_client.service_id,
                    str(exc),
                    address=address,
                    network=network,
                    details={"stage": "fetch"},
                )
            raise
        else:
            if self._monitoring is not None and primary_client is not None:
                self._monitoring.record_api_success(
                    primary_client.service_id,
                    f"{primary_client.service_name}: данные получены",
                    address=address,
                    network=network,
                    details={"transactions": len(result.hops)},
                )
            if not result.hops:
                self.log_output.append(
                    "API не вернуло транзакции — возможно, адрес новый или данные ограничены."
                )
            else:
                self.log_output.append(f"Получено транзакций: {len(result.hops)}")
            self._active_explorer_id = None
            return result

    def _handle_error(self, message: str) -> None:
        self.log_output.append(message)
        if self._monitoring is not None and self._active_explorer_id is None:
            details: dict[str, object] = {"service_name": "Форма нового анализа"}
            if self._active_address:
                details["address"] = self._active_address
            if self._active_network is not None:
                details["network"] = self._active_network.value
            self._monitoring.log(
                "error",
                message,
                source="analysis_ui",
                category="analysis",
                details=details,
            )


class AnalysesPage(QtWidgets.QWidget):
    """List of analyses with filters."""

    open_details = QtCore.Signal(AddressAnalysisResult)

    def __init__(self, store: AnalysisStore) -> None:
        super().__init__()
        self._store = store

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        filter_bar = QtWidgets.QHBoxLayout()
        self.status_filter = QtWidgets.QComboBox()
        self.status_filter.addItems(["Все", "Завершен", "Требует внимания"])
        filter_bar.addWidget(QtWidgets.QLabel("Статус:"))
        filter_bar.addWidget(self.status_filter)

        self.network_filter = QtWidgets.QComboBox()
        self.network_filter.addItem("Все сети")
        for network in SUPPORTED_NETWORKS:
            self.network_filter.addItem(network.name.title())
        filter_bar.addWidget(QtWidgets.QLabel("Сеть:"))
        filter_bar.addWidget(self.network_filter)
        filter_bar.addStretch(1)

        layout.addLayout(filter_bar)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [
                "Адрес",
                "Сеть",
                "Риск",
                "Статус",
                "Последнее обновление",
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table)

        self.status_filter.currentTextChanged.connect(self._refresh_table)
        self.network_filter.currentTextChanged.connect(self._refresh_table)

        self._results: list[AddressAnalysisResult] = list(self._store.results())
        self._display_results: list[AddressAnalysisResult] = []
        self._known_networks = {
            self.network_filter.itemText(i)
            for i in range(self.network_filter.count())
        }
        for result in self._results:
            self._ensure_network_option(result.network)

        self._refresh_table()
        self._store.result_added.connect(self._on_result_added)

    def add_result(
        self,
        result: AddressAnalysisResult,
        briefing: AnalystBriefing | None = None,
    ) -> None:
        self._store.add_result(result, briefing=briefing)

    def _on_result_added(self, result: AddressAnalysisResult) -> None:
        self._results.append(result)
        self._ensure_network_option(result.network)
        self._refresh_table()

    def _ensure_network_option(self, network: Network) -> None:
        name = network.name.title()
        if name not in self._known_networks:
            self.network_filter.addItem(name)
            self._known_networks.add(name)

    def _refresh_table(self) -> None:
        self._display_results = [
            result
            for result in self._results
            if self._matches_filters(result)
        ]
        self.table.setRowCount(len(self._display_results))
        for row, result in enumerate(self._display_results):
            risk_display, _ = _risk_to_display(result.risk_level)
            risk_percent = f"{int(round(result.risk_score * 100))}%"
            status = (
                "Требует внимания"
                if result.risk_level in {"high", "critical"}
                else "Завершен"
            )
            last_seen = max((hop.timestamp for hop in result.hops), default=_current_utc_timestamp())
            timestamp = (
                QtCore.QDateTime.fromSecsSinceEpoch(last_seen, QtCore.QTimeZone.utc())
                .toLocalTime()
                .toString("dd.MM.yyyy HH:mm")
            )
            values = [
                result.address,
                result.network.name.title(),
                f"{risk_display} ({risk_percent})",
                status,
                timestamp,
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))

    def _matches_filters(self, result: AddressAnalysisResult) -> bool:
        status_filter = self.status_filter.currentText()
        if status_filter == "Завершен" and result.risk_level in {"high", "critical"}:
            return False
        if status_filter == "Требует внимания" and result.risk_level not in {"high", "critical"}:
            return False
        network_filter = self.network_filter.currentText()
        if network_filter != "Все сети" and result.network.name.title() != network_filter:
            return False
        return True

    def _open_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        index = rows[0].row()
        if 0 <= index < len(self._display_results):
            self.open_details.emit(self._display_results[index])


@dataclass(frozen=True)
class GraphNode:
    """Represents an address in the relationship graph."""

    node_id: str
    label: str
    category: str
    risk_level: str
    total_flow: float


@dataclass(frozen=True)
class GraphEdge:
    """Connects two nodes in the relationship graph."""

    source: str
    target: str
    relation: str
    volume: float


class GraphNodeItem(QtWidgets.QGraphicsEllipseItem):
    """Visual node with styling based on risk and category."""

    def __init__(self, node: GraphNode, radius: float = 32) -> None:
        super().__init__(-radius, -radius, radius * 2, radius * 2)
        self.node = node
        self.edges: list[GraphEdgeItem] = []
        self.setFlags(
            QtWidgets.QGraphicsItem.ItemIsMovable
            | QtWidgets.QGraphicsItem.ItemIsSelectable
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(1)
        self._update_brush()

        label = QtWidgets.QGraphicsSimpleTextItem(node.label, self)
        label.setBrush(QtGui.QBrush(QtCore.Qt.white))
        label_rect = label.boundingRect()
        label.setPos(-label_rect.width() / 2, -label_rect.height() / 2)

    def _update_brush(self) -> None:
        palette = {
            "Высокий": QtGui.QColor("#f85149"),
            "Средний": QtGui.QColor("#d29922"),
            "Низкий": QtGui.QColor("#238636"),
        }
        color = palette.get(self.node.risk_level, QtGui.QColor("#58a6ff"))
        gradient = QtGui.QRadialGradient(0, 0, 36)
        gradient.setColorAt(0.0, color.lighter(140))
        gradient.setColorAt(1.0, color.darker(150))
        self.setBrush(QtGui.QBrush(gradient))
        pen = QtGui.QPen(QtGui.QColor("#0d1117"))
        pen.setWidth(2)
        self.setPen(pen)

    def hoverEnterEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        tooltip = (
            f"Категория: {self.node.category}\n"
            f"Риск: {self.node.risk_level}\n"
            f"Оборот: {self.node.total_flow:.2f} BTC"
        )
        QtWidgets.QToolTip.showText(event.screenPos(), tooltip)
        super().hoverEnterEvent(event)

    def itemChange(self, change: QtWidgets.QGraphicsItem.GraphicsItemChange, value: QtCore.QVariant) -> QtCore.QVariant:  # noqa: N802
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            for edge in self.edges:
                edge.update_geometry()
        return super().itemChange(change, value)

    def add_edge(self, edge: GraphEdgeItem) -> None:
        self.edges.append(edge)


class GraphEdgeItem(QtWidgets.QGraphicsPathItem):
    """Curved edge with arrow head."""

    def __init__(self, source: GraphNodeItem, target: GraphNodeItem, edge: GraphEdge) -> None:
        super().__init__()
        self.source_item = source
        self.target_item = target
        self.edge = edge
        self.setZValue(0)
        self.setPen(QtGui.QPen(QtGui.QColor("#58a6ff"), 1.6))
        self.arrow_head = QtGui.QPolygonF()
        self.update_geometry()

    def update_geometry(self) -> None:
        src = self.source_item.scenePos()
        dst = self.target_item.scenePos()
        path = QtGui.QPainterPath(src)
        mid = (src + dst) / 2
        offset = QtCore.QPointF(0, -40)
        path.quadTo(mid + offset, dst)
        self.setPath(path)

        arrow_size = 8
        line = QtCore.QLineF(self.path().pointAtPercent(0.95), self.path().pointAtPercent(1.0))
        angle = math.radians(-line.angle())
        dest_point = line.p2()
        arrow_p1 = dest_point + QtCore.QPointF(
            math.sin(angle + math.pi / 3) * arrow_size,
            math.cos(angle + math.pi / 3) * arrow_size,
        )
        arrow_p2 = dest_point + QtCore.QPointF(
            math.sin(angle - math.pi / 3) * arrow_size,
            math.cos(angle - math.pi / 3) * arrow_size,
        )
        arrow = QtGui.QPolygonF([dest_point, arrow_p1, arrow_p2])
        self.arrow_head = arrow

    def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionGraphicsItem, widget: QtWidgets.QWidget | None = None) -> None:
        super().paint(painter, option, widget)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#58a6ff")))
        painter.drawPolygon(self.arrow_head)


class GraphView(QtWidgets.QGraphicsView):
    """Interactive view with wheel zoom and smooth rendering."""

    zoom_changed = QtCore.Signal(int)

    def __init__(self, scene: QtWidgets.QGraphicsScene) -> None:
        super().__init__(scene)
        self.setRenderHints(
            QtGui.QPainter.Antialiasing
            | QtGui.QPainter.TextAntialiasing
            | QtGui.QPainter.SmoothPixmapTransform
        )
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QtGui.QColor("#0d1117"))
        self._zoom_level = 100

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # noqa: N802 - Qt API
        delta = event.angleDelta().y()
        step = 5 if event.modifiers() & QtCore.Qt.ControlModifier else 10
        self.set_zoom(self._zoom_level + step if delta > 0 else self._zoom_level - step)
        event.accept()

    def set_zoom(self, value: int) -> None:
        value = max(30, min(250, value))
        self._zoom_level = value
        self.resetTransform()
        self.scale(value / 100.0, value / 100.0)
        self.zoom_changed.emit(self._zoom_level)

    def zoom_level(self) -> int:
        return self._zoom_level


class GraphWidget(QtWidgets.QWidget):
    """Full graph widget with controls for zoom and filtering."""

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(10)

        controls.addWidget(QtWidgets.QLabel("Риск:"))
        self.risk_filter = QtWidgets.QComboBox()
        self.risk_filter.addItems(["Все", "Высокий", "Средний", "Низкий"])
        controls.addWidget(self.risk_filter)

        controls.addWidget(QtWidgets.QLabel("Категория:"))
        self.category_filter = QtWidgets.QComboBox()
        self.category_filter.addItems(["Все", "Mixer", "Exchange", "Wallet", "Merchant"])
        controls.addWidget(self.category_filter)

        controls.addStretch(1)

        self.zoom_out_btn = QtWidgets.QToolButton()
        self.zoom_out_btn.setText("-")
        controls.addWidget(self.zoom_out_btn)

        self.zoom_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.zoom_slider.setRange(30, 250)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(160)
        controls.addWidget(self.zoom_slider)

        self.zoom_in_btn = QtWidgets.QToolButton()
        self.zoom_in_btn.setText("+")
        controls.addWidget(self.zoom_in_btn)

        layout.addLayout(controls)

        self.scene = QtWidgets.QGraphicsScene()
        self.scene.setSceneRect(-400, -300, 800, 600)
        self.view = GraphView(self.scene)
        layout.addWidget(self.view)

        self.nodes: dict[str, GraphNodeItem] = {}
        self.edges: list[GraphEdgeItem] = []
        self._empty_label: QtWidgets.QGraphicsTextItem | None = None

        self.load_graph([], [])
        self._connect_signals()

    def _connect_signals(self) -> None:
        self.risk_filter.currentTextChanged.connect(self._apply_filters)
        self.category_filter.currentTextChanged.connect(self._apply_filters)
        self.zoom_slider.valueChanged.connect(self.view.set_zoom)
        self.zoom_in_btn.clicked.connect(lambda: self.zoom_slider.setValue(self.zoom_slider.value() + 10))
        self.zoom_out_btn.clicked.connect(lambda: self.zoom_slider.setValue(self.zoom_slider.value() - 10))
        self.view.zoom_changed.connect(self._sync_zoom_slider)

    def load_graph(self, nodes: Iterable[GraphNode], edges: Iterable[GraphEdge]) -> None:
        self.scene.clear()
        self.nodes.clear()
        self.edges.clear()
        self._empty_label = None

        nodes = list(nodes)
        if not nodes:
            text_item = self.scene.addText("Нет данных для отображения")
            text_item.setDefaultTextColor(QtGui.QColor("#8b949e"))
            bounds = text_item.boundingRect()
            text_item.setPos(-bounds.width() / 2, -bounds.height() / 2)
            self._empty_label = text_item
            return

        radius = 200
        for index, node in enumerate(nodes):
            angle = (2 * math.pi * index) / max(len(nodes), 1)
            x = math.cos(angle) * radius
            y = math.sin(angle) * radius
            item = GraphNodeItem(node)
            item.setPos(x, y)
            self.scene.addItem(item)
            self.nodes[node.node_id] = item

        for edge in edges:
            source_item = self.nodes.get(edge.source)
            target_item = self.nodes.get(edge.target)
            if not source_item or not target_item:
                continue
            edge_item = GraphEdgeItem(source_item, target_item, edge)
            self.scene.addItem(edge_item)
            source_item.add_edge(edge_item)
            target_item.add_edge(edge_item)
            self.edges.append(edge_item)

        for item in self.nodes.values():
            halo = QtWidgets.QGraphicsEllipseItem(-40, -40, 80, 80)
            halo.setBrush(QtGui.QBrush(QtGui.QColor(88, 166, 255, 40)))
            halo.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            halo.setParentItem(item)
            halo.setZValue(-1)

        self._apply_filters()

    def _apply_filters(self) -> None:
        risk = self.risk_filter.currentText()
        category = self.category_filter.currentText()

        if not self.nodes:
            return

        for node_id, item in self.nodes.items():
            node = item.node
            visible = True
            if risk != "Все" and node.risk_level != risk:
                visible = False
            if category != "Все" and node.category != category:
                visible = False
            item.setVisible(visible)

        for edge_item in self.edges:
            source_visible = edge_item.source_item.isVisible()
            target_visible = edge_item.target_item.isVisible()
            edge_item.setVisible(source_visible and target_visible)

    def load_from_analysis(self, analysis: AddressAnalysisResult) -> None:
        main_label, filter_label = _risk_to_display(analysis.risk_level)
        total_in = sum(hop.amount for hop in analysis.hops if hop.to_address == analysis.address)
        total_out = sum(
            hop.amount for hop in analysis.hops if hop.from_address == analysis.address
        )
        nodes: list[GraphNode] = [
            GraphNode(
                node_id=analysis.address,
                label=f"{_short_address(analysis.address)}\n{main_label}",
                category="Wallet",
                risk_level=filter_label,
                total_flow=total_in + total_out,
            )
        ]

        aggregates: dict[str, dict[str, float]] = defaultdict(lambda: {"incoming": 0.0, "outgoing": 0.0})
        for hop in analysis.hops:
            if hop.from_address == analysis.address:
                aggregates[hop.to_address]["incoming"] += hop.amount
            elif hop.to_address == analysis.address:
                aggregates[hop.from_address]["outgoing"] += hop.amount

        sorted_counterparties = sorted(
            aggregates.items(),
            key=lambda item: item[1]["incoming"] + item[1]["outgoing"],
            reverse=True,
        )

        edges: list[GraphEdge] = []
        for counterparty, flow in sorted_counterparties[:12]:
            total_flow = flow["incoming"] + flow["outgoing"]
            risk_level = "Средний" if total_flow > 1.0 else "Низкий"
            nodes.append(
                GraphNode(
                    node_id=counterparty,
                    label=_short_address(counterparty),
                    category="Wallet",
                    risk_level=risk_level,
                    total_flow=total_flow,
                )
            )
            if flow["incoming"] > 0:
                edges.append(
                    GraphEdge(
                        source=analysis.address,
                        target=counterparty,
                        relation="Вывод",
                        volume=flow["incoming"],
                    )
                )
            if flow["outgoing"] > 0:
                edges.append(
                    GraphEdge(
                        source=counterparty,
                        target=analysis.address,
                        relation="Ввод",
                        volume=flow["outgoing"],
                    )
                )

        self.load_graph(nodes, edges)

    def _sync_zoom_slider(self, value: int) -> None:
        if self.zoom_slider.value() == value:
            return
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(value)
        self.zoom_slider.blockSignals(False)




class AnalysisDetailPage(QtWidgets.QWidget):
    """Detailed view with tabs for overview, graph, transactions, forecasts, report."""

    def __init__(
        self,
        store: AnalysisStore | None = None,
        analyst: ArtificialAnalyst | None = None,
        monitoring: MonitoringService | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._analyst = analyst
        self._monitoring = monitoring
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        self.header = QtWidgets.QLabel("Адрес: — | Сеть: — | Обновлено: —")
        self.header.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 600;")
        layout.addWidget(self.header)

        self.tabs = QtWidgets.QTabWidget()
        self.overview_tab = self._create_overview()
        self.tabs.addTab(self.overview_tab, "Обзор")
        self.graph_widget = GraphWidget()
        self.tabs.addTab(self.graph_widget, "Граф")
        self.transactions_tab = self._create_transactions_tab()
        self.tabs.addTab(self.transactions_tab, "Транзакции")
        self.tabs.addTab(self._create_forecast_tab(), "Прогнозы")
        self.tabs.addTab(self._create_report_tab(), "Отчет")
        layout.addWidget(self.tabs)

        self.current_analysis: AddressAnalysisResult | None = None
        self.current_briefing: AnalystBriefing | None = None
        if self._monitoring is not None:
            self._monitoring.event_recorded.connect(self._on_monitoring_event)
            self._monitoring.watch_added.connect(self._on_monitoring_event)

    def set_analysis(
        self,
        analysis: AddressAnalysisResult,
        briefing: AnalystBriefing | None = None,
    ) -> None:
        self.current_analysis = analysis
        risk_display, _ = _risk_to_display(analysis.risk_level)
        updated_time = QtCore.QDateTime.currentDateTime().toString("dd.MM.yyyy HH:mm:ss")
        self.header.setText(
            f"Адрес: {analysis.address} | Сеть: {analysis.network.name.title()} | Обновлено: {updated_time}"
        )

        score_percent = max(0, min(100, int(round(analysis.risk_score * 100))))
        self.risk_progress.setValue(score_percent)
        self.risk_progress.setFormat(f"{score_percent}% ({risk_display} риск)")

        notes = analysis.notes or ["Эвристики не выявили критических сигналов."]
        self.risk_notes.setPlainText("\n".join(notes))

        self.services_list.clear()
        services_used = list(analysis.sources) if analysis.sources else []
        if "Искусственный аналитик ArcheBlow" not in services_used:
            services_used.append("Искусственный аналитик ArcheBlow")
        if not services_used:
            services_used = ["Источники данных не определены"]
        self.services_list.addItems(services_used)

        self._render_monitoring_section(analysis)

        self.graph_widget.load_from_analysis(analysis)
        self._populate_transactions(analysis)

        self.current_briefing = self._resolve_briefing(analysis, briefing)
        self._render_briefing(self.current_briefing)

    def _create_overview(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setSpacing(12)

        risk_box = QtWidgets.QGroupBox("Индекс риска")
        risk_layout = QtWidgets.QVBoxLayout()
        self.risk_progress = QtWidgets.QProgressBar()
        self.risk_progress.setRange(0, 100)
        self.risk_progress.setValue(0)
        self.risk_progress.setFormat("—")
        risk_layout.addWidget(self.risk_progress)
        self.risk_notes = QtWidgets.QTextEdit()
        self.risk_notes.setReadOnly(True)
        self.risk_notes.setPlaceholderText("Комментарии по рискам появятся после анализа…")
        risk_layout.addWidget(self.risk_notes)
        risk_box.setLayout(risk_layout)
        layout.addWidget(risk_box)

        services_box = QtWidgets.QGroupBox("Задействованные сервисы")
        services_layout = QtWidgets.QVBoxLayout()
        self.services_list = QtWidgets.QListWidget()
        services_layout.addWidget(self.services_list)
        services_box.setLayout(services_layout)
        layout.addWidget(services_box)

        ai_box = QtWidgets.QGroupBox("Искусственный аналитик")
        ai_layout = QtWidgets.QVBoxLayout()
        self.ai_summary = QtWidgets.QTextEdit()
        self.ai_summary.setReadOnly(True)
        self.ai_summary.setPlaceholderText(
            "После завершения проверки появится сводка искусственного аналитика."
        )
        ai_layout.addWidget(self.ai_summary)
        self.ai_confidence_label = QtWidgets.QLabel("Уверенность: —")
        self.ai_confidence_label.setStyleSheet("color: #8b949e;")
        ai_layout.addWidget(self.ai_confidence_label)

        ai_layout.addWidget(QtWidgets.QLabel("Рекомендации:"))
        self.ai_actions = QtWidgets.QListWidget()
        self.ai_actions.setAlternatingRowColors(True)
        ai_layout.addWidget(self.ai_actions)

        ai_layout.addWidget(QtWidgets.QLabel("Ключевые факты:"))
        self.ai_highlights = QtWidgets.QListWidget()
        self.ai_highlights.setAlternatingRowColors(True)
        ai_layout.addWidget(self.ai_highlights)

        ai_layout.addWidget(QtWidgets.QLabel("Тревоги:"))
        self.ai_alerts = QtWidgets.QListWidget()
        self.ai_alerts.setAlternatingRowColors(True)
        ai_layout.addWidget(self.ai_alerts)

        ai_box.setLayout(ai_layout)
        layout.addWidget(ai_box)

        monitoring_box = QtWidgets.QGroupBox("Мониторинг адреса")
        monitoring_layout = QtWidgets.QVBoxLayout()
        self.monitoring_status = QtWidgets.QLabel("Мониторинг не активирован.")
        self.monitoring_status.setStyleSheet("color: #8b949e;")
        monitoring_layout.addWidget(self.monitoring_status)
        self.monitoring_events = QtWidgets.QListWidget()
        self.monitoring_events.setAlternatingRowColors(True)
        monitoring_layout.addWidget(self.monitoring_events)
        monitoring_box.setLayout(monitoring_layout)
        layout.addWidget(monitoring_box)

        return widget

    def _resolve_briefing(
        self,
        analysis: AddressAnalysisResult,
        briefing: AnalystBriefing | None,
    ) -> AnalystBriefing | None:
        if briefing is not None:
            return briefing
        if self._store is not None:
            stored = self._store.briefing_for(analysis.address, analysis.network)
            if stored is not None:
                return stored
        if self._analyst is not None:
            return self._analyst.generate_briefing(analysis)
        return None

    def _render_briefing(self, briefing: AnalystBriefing | None) -> None:
        self.ai_summary.clear()
        self.ai_actions.clear()
        self.ai_highlights.clear()
        self.ai_alerts.clear()

        if briefing is None:
            self.ai_summary.setPlainText(
                "Искусственный аналитик сформирует заключение после выполнения нового анализа."
            )
            self.ai_confidence_label.setText("Уверенность: —")
            return

        self.ai_summary.setPlainText(briefing.summary)
        self.ai_confidence_label.setText(
            f"Уверенность: {int(round(briefing.confidence * 100))}%"
        )

        if briefing.recommendations:
            for item in briefing.recommendations:
                actions_text = "; ".join(item.actions) if item.actions else "Дополнительные действия не требуются"
                self.ai_actions.addItem(
                    f"[{item.priority}] {item.title} — {item.rationale}. Действия: {actions_text}"
                )
        else:
            self.ai_actions.addItem("Рекомендации не требуются.")

        if briefing.highlights:
            for highlight in briefing.highlights:
                self.ai_highlights.addItem(highlight)
        else:
            self.ai_highlights.addItem("Дополнительных фактов не выявлено.")

        if briefing.alerts:
            for alert in briefing.alerts:
                self.ai_alerts.addItem(alert)
        else:
            self.ai_alerts.addItem("Тревожных событий не обнаружено.")

    def _render_monitoring_section(self, analysis: AddressAnalysisResult) -> None:
        if not hasattr(self, "monitoring_status"):
            return
        if self._monitoring is None:
            self.monitoring_status.setText("Система мониторинга не активирована.")
            self.monitoring_events.clear()
            self.monitoring_events.addItem("Мониторинг недоступен.")
            return

        watches = self._monitoring.watch_for(analysis.address, analysis.network)
        if not watches:
            self.monitoring_status.setText("Адрес не находится под активным мониторингом.")
        else:
            parts = []
            for watch in watches:
                expiry = (
                    QtCore.QDateTime.fromSecsSinceEpoch(
                        watch.expires_at, QtCore.QTimeZone.utc()
                    )
                    .toLocalTime()
                    .toString("dd.MM.yyyy HH:mm")
                )
                parts.append(f"до {expiry}")
            self.monitoring_status.setText(
                "Активный мониторинг: " + ", ".join(parts)
            )

        events = self._monitoring.events_for(analysis.address, analysis.network, limit=5)
        self.monitoring_events.clear()
        if not events:
            self.monitoring_events.addItem("Журнал событий пуст.")
            return
        for event in events:
            ts_text = (
                QtCore.QDateTime.fromSecsSinceEpoch(event.timestamp, QtCore.QTimeZone.utc())
                .toLocalTime()
                .toString("dd.MM HH:mm")
            )
            service_name = event.details.get("service_name") or _service_display_name(
                event.details.get("service_id", event.source)
            )
            self.monitoring_events.addItem(
                f"{ts_text}: [{event.level.upper()}] {service_name} — {event.message}"
            )

    def _on_monitoring_event(self, _event: object) -> None:
        if self.current_analysis is not None:
            self._render_monitoring_section(self.current_analysis)

    def _create_transactions_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setSpacing(12)

        filter_bar = QtWidgets.QHBoxLayout()
        date_filter = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        amount_filter = QtWidgets.QDoubleSpinBox()
        amount_filter.setPrefix("> ")
        amount_filter.setMaximum(10_000)
        status_filter = QtWidgets.QComboBox()
        status_filter.addItems(["Все", "Подозрительные", "Наблюдение"])
        filter_bar.addWidget(QtWidgets.QLabel("Дата с:"))
        filter_bar.addWidget(date_filter)
        filter_bar.addWidget(QtWidgets.QLabel("Сумма:"))
        filter_bar.addWidget(amount_filter)
        filter_bar.addWidget(QtWidgets.QLabel("Статус:"))
        filter_bar.addWidget(status_filter)
        filter_bar.addStretch(1)
        layout.addLayout(filter_bar)

        self.transactions_table = QtWidgets.QTableWidget(0, 6)
        self.transactions_table.setHorizontalHeaderLabels(
            [
                "TX Hash",
                "От",
                "К",
                "Сумма (BTC)",
                "Статус",
                "Время",
            ]
        )
        self.transactions_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.transactions_table)

        return widget

    def _populate_transactions(self, analysis: AddressAnalysisResult) -> None:
        hops = list(analysis.hops)[:200]
        self.transactions_table.setRowCount(len(hops))

        mixer_addresses = {
            str(match.evidence.get("match"))
            for match in analysis.mixers
            if isinstance(match.evidence.get("match"), str)
        }

        for row, hop in enumerate(hops):
            amount_item = QtWidgets.QTableWidgetItem(f"{hop.amount:.8f}")
            amount_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            direction = "Исходящая" if hop.from_address == analysis.address else "Входящая"
            flag = "Миксер" if hop.to_address in mixer_addresses or hop.from_address in mixer_addresses else "-"
            timestamp = QtCore.QDateTime.fromSecsSinceEpoch(
                hop.timestamp, QtCore.QTimeZone.utc()
            ).toLocalTime()

            self.transactions_table.setItem(row, 0, QtWidgets.QTableWidgetItem(hop.tx_hash))
            self.transactions_table.setItem(row, 1, QtWidgets.QTableWidgetItem(_short_address(hop.from_address)))
            self.transactions_table.setItem(row, 2, QtWidgets.QTableWidgetItem(_short_address(hop.to_address)))
            self.transactions_table.setItem(row, 3, amount_item)
            self.transactions_table.setItem(row, 4, QtWidgets.QTableWidgetItem(flag if flag != "-" else direction))
            self.transactions_table.setItem(row, 5, QtWidgets.QTableWidgetItem(timestamp.toString("yyyy-MM-dd HH:mm")))

        if not hops:
            self.transactions_table.setRowCount(1)
            self.transactions_table.setItem(0, 0, QtWidgets.QTableWidgetItem("—"))
            self.transactions_table.setItem(0, 1, QtWidgets.QTableWidgetItem("Нет данных"))
            self.transactions_table.setItem(0, 2, QtWidgets.QTableWidgetItem(""))
            self.transactions_table.setItem(0, 3, QtWidgets.QTableWidgetItem(""))
            self.transactions_table.setItem(0, 4, QtWidgets.QTableWidgetItem(""))
            self.transactions_table.setItem(0, 5, QtWidgets.QTableWidgetItem(""))

    def _create_forecast_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        info = QtWidgets.QLabel(
            "Прогнозные модели готовятся к интеграции."
            "\nСценарии будут доступны после обучения моделей."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #8b949e; font-size: 13px;")
        layout.addWidget(info)

        card = QtWidgets.QGroupBox("Запланированные сценарии")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.addWidget(QtWidgets.QLabel("• Детекция аномалий по графу связей"))
        card_layout.addWidget(QtWidgets.QLabel("• Прогнозирование рисковых потоков"))
        card_layout.addWidget(QtWidgets.QLabel("• Индикаторы эскалации для команд мониторинга"))
        layout.addWidget(card)

        return widget

    def _create_report_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        instructions = QtWidgets.QLabel(
            "Настройте структуру отчета и выберите формат экспорта."
        )
        layout.addWidget(instructions)

        form = QtWidgets.QFormLayout()
        section_selector = QtWidgets.QListWidget()
        section_selector.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        section_selector.addItems(
            [
                "Резюме риска",
                "Транзакционная активность",
                "Граф связей",
                "Связанные адреса",
                "Инциденты и уведомления",
            ]
        )
        form.addRow("Разделы", section_selector)

        format_combo = QtWidgets.QComboBox()
        format_combo.addItems(["PDF", "JSON", "CSV"])
        form.addRow("Формат", format_combo)

        layout.addLayout(form)

        export_button = QtWidgets.QPushButton("Экспорт отчета")
        export_button.setStyleSheet(
            "background: #1f6feb; color: #ffffff; border-radius: 10px; padding: 10px 18px;"
        )
        layout.addWidget(export_button, alignment=QtCore.Qt.AlignRight)

        preview = QtWidgets.QTextEdit()
        preview.setReadOnly(True)
        preview.setPlaceholderText("Предпросмотр отчета появится здесь…")
        layout.addWidget(preview)

        return widget


class IntegrationsPage(QtWidgets.QWidget):
    """Displays API integrations with status badges and actions."""

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QtWidgets.QLabel("Интеграции")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        services = [
            ("blockchain_com", "Активен", "∞", "Проверить лимит"),
            ("blockcypher", "Активен", "60%", "Синхронизировать"),
            ("etherscan", "Требует ключ", "--", "Добавить ключ"),
            ("polygonscan", "Требует ключ", "--", "Добавить ключ"),
            ("trongrid", "Требует ключ", "--", "Добавить ключ"),
            ("blockchair", "Активен", "75%", "Обновить токен"),
            ("chainz", "Ограничен", "90%", "Проверить лимиты"),
            ("coingecko", "Ошибка", "--", "Просмотр логов"),
            ("ofac_watchlist", "Активен", "--", "Обновить"),
            ("heuristic_mixer", "Активен", "--", "Синхронизировать"),
            ("ai_analyst", "Активен", "N/A", "Открыть инструкцию"),
            ("monitoring_webhook", "Не настроен", "--", "Добавить webhook"),
        ]

        table = QtWidgets.QTableWidget(len(services), 5)
        table.setHorizontalHeaderLabels([
            "Сервис",
            "Статус",
            "API ключ",
            "Лимит",
            "Действия",
        ])
        for row, (service_id, status, limit, action) in enumerate(services):
            entry = API_SERVICE_KEYS.get(service_id)
            name = entry.display_name if entry else service_id
            masked_key = get_masked_key(service_id)
            values = [name, status, masked_key, limit, action]
            for column, value in enumerate(values):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

        help_box = QtWidgets.QGroupBox("Как добавить ключ")
        help_layout = QtWidgets.QVBoxLayout()
        help_text = QtWidgets.QLabel(
            "Добавьте секреты в переменные окружения или создайте файл "
            "api_keys.env/.env рядом с приложением. Каждая строка должна быть в "
            "формате ИМЯ=значение. После изменения перезапустите приложение."
        )
        help_text.setWordWrap(True)
        help_layout.addWidget(help_text)

        example = QtWidgets.QPlainTextEdit()
        example.setReadOnly(True)
        example.setMaximumHeight(120)
        example.setPlainText(
            "# пример файла api_keys.env\n"
            "BLOCKCHAIN_COM_API_KEY=ваш_ключ\n"
            "BLOCKCYPHER_API_KEY=...\n"
            "ETHERSCAN_API_KEY=...\n"
            "TRONGRID_API_KEY=...\n"
            "POLYGONSCAN_API_KEY=...\n"
            "ARCHEBLOW_AI_ANALYST=N/A"
            "\nARCHEBLOW_MONITORING_WEBHOOK=https://hooks.example/api"
        )
        help_layout.addWidget(example)
        help_box.setLayout(help_layout)
        layout.addWidget(help_box)

        tips = QtWidgets.QGroupBox("Подсказки")
        tips_layout = QtWidgets.QVBoxLayout()
        tips_list = QtWidgets.QListWidget()
        tips_list.addItems(
            [
                "Используйте бесплатные лимиты CoinGecko для курса валют.",
                "Обновляйте ключи Chainz каждые 30 дней.",
                "Настройте webhook для уведомлений об ошибках API.",
            ]
        )
        tips_layout.addWidget(tips_list)
        tips.setLayout(tips_layout)
        layout.addWidget(tips)


class ReportsPage(QtWidgets.QWidget):
    """Provides quick access to generated reports."""

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QtWidgets.QLabel("Отчеты")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        self.list = QtWidgets.QListWidget()
        self.list.addItems(
            [
                "Анализ 0xACF8 — высокий риск",
                "Анализ 12ab34 — мониторинг",
                "Сводка отдела — неделя 12",
            ]
        )
        layout.addWidget(self.list)

        export_layout = QtWidgets.QHBoxLayout()
        export_layout.addWidget(QtWidgets.QLabel("Экспорт выбранного:"))
        export_combo = QtWidgets.QComboBox()
        export_combo.addItems(["PDF", "DOCX", "JSON"])
        export_layout.addWidget(export_combo)
        export_button = QtWidgets.QPushButton("Экспортировать")
        export_button.setStyleSheet(
            "background: #1f6feb; color: #ffffff; border-radius: 10px; padding: 10px 18px;"
        )
        export_layout.addWidget(export_button)
        export_layout.addStretch(1)
        layout.addLayout(export_layout)

        audit_log = QtWidgets.QTextEdit()
        audit_log.setReadOnly(True)
        audit_log.setPlaceholderText("История экспорта отчетов будет отображаться здесь…")
        layout.addWidget(audit_log)


class SettingsPage(QtWidgets.QWidget):
    """Configuration forms for workspaces, notifications, and exports."""

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QtWidgets.QLabel("Настройки")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        form = QtWidgets.QFormLayout()

        workspace_box = QtWidgets.QComboBox()
        workspace_box.addItems(["Compliance HQ", "R&D Sandbox", "Тестовая команда"])
        form.addRow("Рабочее пространство", workspace_box)

        notifications = QtWidgets.QGroupBox("Уведомления")
        notif_layout = QtWidgets.QVBoxLayout()
        notif_layout.addWidget(QtWidgets.QCheckBox("Email"))
        notif_layout.addWidget(QtWidgets.QCheckBox("Webhook"))
        notif_layout.addWidget(QtWidgets.QCheckBox("Мессенджер"))
        notifications.setLayout(notif_layout)
        form.addRow(notifications)

        export_path = QtWidgets.QLineEdit("/var/reports")
        form.addRow("Путь экспорта", export_path)

        schedule = QtWidgets.QTimeEdit(QtCore.QTime.currentTime())
        form.addRow("Расписание задач", schedule)

        database_box = QtWidgets.QComboBox()
        database_box.addItems(["PostgreSQL", "Neo4j", "Redis"])
        form.addRow("База данных", database_box)

        layout.addLayout(form)

        analyst_box = QtWidgets.QGroupBox("Искусственный аналитик ArcheBlow")
        analyst_layout = QtWidgets.QVBoxLayout()
        analyst_description = QtWidgets.QLabel(analyst_playbook())
        analyst_description.setWordWrap(True)
        analyst_layout.addWidget(analyst_description)
        analyst_layout.addWidget(QtWidgets.QCheckBox("Автоматически формировать рекомендации"))
        guidance = QtWidgets.QPlainTextEdit()
        guidance.setReadOnly(True)
        guidance.setPlainText(
            "1. Запускайте анализ через раздел 'Новый анализ'.\n"
            "2. После завершения проверяйте сводку на вкладке 'Обзор'.\n"
            "3. Выполняйте шаги из списка рекомендаций и фиксируйте их статус.\n"
            "4. Используйте тревоги для настройки уведомлений и ручного расследования."
        )
        guidance.setMaximumHeight(120)
        analyst_layout.addWidget(guidance)
        analyst_box.setLayout(analyst_layout)
        layout.addWidget(analyst_box)

        save_button = QtWidgets.QPushButton("Сохранить изменения")
        save_button.setStyleSheet(
            "background: #238636; color: #ffffff; border-radius: 10px; padding: 10px 18px;"
        )
        layout.addWidget(save_button, alignment=QtCore.Qt.AlignRight)


class MainWindow(QtWidgets.QMainWindow):
    """Top-level window that composes all application sections."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ArcheBlow Desktop")
        self.setMinimumSize(1280, 800)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        root_layout = QtWidgets.QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        nav_items = [
            NavItem("Дашборд", "dashboard", "📊"),
            NavItem("Новый анализ", "new_analysis", "➕"),
            NavItem("Анализы", "analyses", "🧾"),
            NavItem("Интеграции", "integrations", "🧩"),
            NavItem("Отчеты", "reports", "📁"),
            NavItem("Настройки", "settings", "⚙️"),
        ]
        self.navigation = NavigationPanel(nav_items)
        self.navigation.selection_changed.connect(self._switch_page)
        root_layout.addWidget(self.navigation)

        content_area = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content_area)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.store = AnalysisStore()
        self.analyst = ArtificialAnalyst()
        webhook_url = get_api_key("monitoring_webhook")
        self.monitoring = MonitoringService(webhook_url=webhook_url)

        self.top_bar = TopBar(self.store, self.monitoring)
        self.top_bar.request_search.connect(self._handle_search)
        content_layout.addWidget(self.top_bar)

        self.pages = QtWidgets.QStackedWidget()
        self.dashboard_page = DashboardPage(self.store, self.monitoring)
        self.new_analysis_page = NewAnalysisPage(self.monitoring)
        self.analyses_page = AnalysesPage(self.store)
        self.detail_page = AnalysisDetailPage(self.store, self.analyst, self.monitoring)
        self.integrations_page = IntegrationsPage()
        self.reports_page = ReportsPage()
        self.settings_page = SettingsPage()

        self.pages.addWidget(self.dashboard_page)
        self.pages.addWidget(self.new_analysis_page)
        self.pages.addWidget(self.analyses_page)
        self.pages.addWidget(self.detail_page)
        self.pages.addWidget(self.integrations_page)
        self.pages.addWidget(self.reports_page)
        self.pages.addWidget(self.settings_page)
        content_layout.addWidget(self.pages)

        root_layout.addWidget(content_area)

        self.navigation.set_active("dashboard")
        self.pages.setCurrentWidget(self.dashboard_page)

        self.new_analysis_page.analysis_completed.connect(self._analysis_completed)
        self.analyses_page.open_details.connect(self._open_analysis_details)

        self._style_application()

    def _style_application(self) -> None:
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#010409"))
        palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#c9d1d9"))
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor("#0d1117"))
        palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#161b22"))
        palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor("#f0f6fc"))
        palette.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor("#0d1117"))
        palette.setColor(QtGui.QPalette.Text, QtGui.QColor("#c9d1d9"))
        palette.setColor(QtGui.QPalette.Button, QtGui.QColor("#21262d"))
        palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#c9d1d9"))
        palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor("#f85149"))
        palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#1f6feb"))
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
        self.setPalette(palette)
        self.setStyleSheet(
            """
            QMainWindow { background-color: #010409; }
            QLabel { color: #c9d1d9; }
            QGroupBox { color: #c9d1d9; border: 1px solid #30363d; border-radius: 12px; padding: 16px; }
            QTabBar::tab { background: #161b22; padding: 8px 16px; border: 1px solid #30363d; border-bottom: none; }
            QTabBar::tab:selected { background: #1f6feb; color: #ffffff; }
            QTabWidget::pane { border: 1px solid #30363d; border-radius: 0 0 12px 12px; }
            QListWidget, QTextEdit, QPlainTextEdit { background: #0d1117; border: 1px solid #30363d; border-radius: 12px; color: #c9d1d9; }
            QTableWidget { background: #0d1117; border: 1px solid #30363d; border-radius: 12px; gridline-color: #30363d; }
            QHeaderView::section { background: #161b22; color: #8b949e; border: none; padding: 6px; }
            QPushButton { color: #c9d1d9; }
            QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit { background: #161b22; border: 1px solid #30363d; border-radius: 8px; color: #c9d1d9; padding: 6px; }
            QCheckBox { color: #c9d1d9; }
            """
        )

    def _switch_page(self, page_id: str) -> None:
        mapping = {
            "dashboard": self.dashboard_page,
            "new_analysis": self.new_analysis_page,
            "analyses": self.analyses_page,
            "integrations": self.integrations_page,
            "reports": self.reports_page,
            "settings": self.settings_page,
        }
        widget = mapping.get(page_id)
        if widget is not None:
            self.pages.setCurrentWidget(widget)

    def _handle_search(self, query: str) -> None:
        if not query:
            return
        QtWidgets.QMessageBox.information(self, "Поиск", f"Результаты поиска по запросу: {query}")

    def _analysis_completed(self, analysis: AddressAnalysisResult) -> None:
        briefing = self.analyst.generate_briefing(analysis)
        self.store.add_result(analysis, briefing=briefing)
        risk_display, _ = _risk_to_display(analysis.risk_level)
        recommendation_line = ""
        if briefing.recommendations:
            primary = briefing.recommendations[0]
            recommendation_line = (
                f"\nРекомендация аналитика: {primary.title} (приоритет {primary.priority})."
            )
        QtWidgets.QMessageBox.information(
            self,
            "Анализ завершен",
            (
                f"Анализ адреса {analysis.address} ({analysis.network.name.title()}) завершен.\n"
                f"Итоговый уровень риска: {risk_display}."
                f"{recommendation_line}"
            ),
        )
        self.detail_page.set_analysis(analysis, briefing)
        self.navigation.set_active("analyses")
        self.pages.setCurrentWidget(self.analyses_page)

    def _open_analysis_details(self, analysis: AddressAnalysisResult) -> None:
        briefing = self.store.briefing_for(analysis.address, analysis.network)
        self.detail_page.set_analysis(analysis, briefing)
        if self.pages.indexOf(self.detail_page) == -1:
            self.pages.addWidget(self.detail_page)
        self.pages.setCurrentWidget(self.detail_page)


def main() -> None:
    """Entry point that starts the Qt application with qasync."""

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    window.show()

    app.aboutToQuit.connect(loop.stop)
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
