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

import httpx
from PySide6 import QtCore, QtGui, QtWidgets
from qasync import QEventLoop, asyncSlot

from archeblow_service import (
    AddressAnalysisResult,
    ArcheBlowAnalyzer,
    HeuristicMixerClient,
    Network,
    TransactionHop,
)


class UnsupportedNetworkError(RuntimeError):
    """Raised when the selected network lacks a configured public API."""


class BlockCypherExplorerClient:
    """Explorer client that pulls transactions from the free BlockCypher API."""

    _BASE_ENDPOINTS: Mapping[Network, str] = {
        Network.BITCOIN: "https://api.blockcypher.com/v1/btc/main",
    }

    def __init__(self, network: Network, *, session: httpx.AsyncClient | None = None) -> None:
        if network not in self._BASE_ENDPOINTS:
            raise UnsupportedNetworkError(
                f"Сеть {network.value} не поддерживается публичным API BlockCypher."
            )
        self.network = network
        self._base_url = self._BASE_ENDPOINTS[network]
        self._session = session

    async def fetch_transaction_hops(self, address: str) -> Sequence[TransactionHop]:
        url = f"{self._base_url}/addrs/{address}/full"
        params = {"limit": 50, "txlimit": 50}
        close_session = False
        session = self._session
        if session is None:
            timeout = httpx.Timeout(20.0, connect=10.0, read=20.0)
            session = httpx.AsyncClient(timeout=timeout)
            close_session = True
        try:
            response = await session.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - network errors handled at runtime
            raise RuntimeError(
                f"BlockCypher API вернул ошибку {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:  # pragma: no cover - network errors handled at runtime
            raise RuntimeError("Ошибка сети при обращении к BlockCypher API") from exc
        finally:
            if close_session:
                await session.aclose()

        payload = response.json()
        transactions = payload.get("txs", [])
        hops: list[TransactionHop] = []

        for tx in transactions:
            tx_hash = tx.get("hash", "")
            timestamp = _parse_timestamp(tx.get("confirmed") or tx.get("received"))
            inputs = tx.get("inputs", [])
            outputs = tx.get("outputs", [])
            for inp in inputs:
                from_addr = _first_address(inp)
                for out in outputs:
                    to_addr = _first_address(out)
                    amount_satoshi = out.get("value") or 0
                    amount_btc = amount_satoshi / 100_000_000 if amount_satoshi else 0.0
                    hop = TransactionHop(
                        tx_hash=tx_hash,
                        from_address=from_addr,
                        to_address=to_addr,
                        amount=amount_btc,
                        timestamp=timestamp,
                        metadata={"block_height": tx.get("block_height")},
                    )
                    hops.append(hop)

        hops.sort(key=lambda hop: hop.timestamp, reverse=True)
        # Limit to keep the UI responsive.
        return hops[:200]


def _parse_timestamp(value: str | None) -> int:
    if not value:
        return int(_dt.datetime.utcnow().timestamp())
    try:
        return int(_dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(_dt.datetime.utcnow().timestamp())


def _first_address(data: Mapping[str, object]) -> str:
    addresses = data.get("addresses")
    if isinstance(addresses, list) and addresses:
        return str(addresses[0])
    if isinstance(addresses, str):
        return addresses
    return "Неизвестно"


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
    """Displays the current sync status and background task metrics."""

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("color: #8b949e;")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(16)

        sync_icon = QtWidgets.QLabel("🔄")
        layout.addWidget(sync_icon)
        self.sync_label = QtWidgets.QLabel("Синхронизация: активна")
        layout.addWidget(self.sync_label)

        tasks_icon = QtWidgets.QLabel("📊")
        layout.addWidget(tasks_icon)
        self.task_label = QtWidgets.QLabel("Фоновые задачи: 2 в работе")
        layout.addWidget(self.task_label)

        layout.addStretch(1)


class NotificationCenter(QtWidgets.QFrame):
    """Notification icon with dropdown placeholder."""

    def __init__(self) -> None:
        super().__init__()
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
        layout.addWidget(self.button)

        self.counter = QtWidgets.QLabel("3")
        self.counter.setStyleSheet("color: #ffffff; background: #d29922; padding: 2px 6px; border-radius: 8px;")
        layout.addWidget(self.counter)


class TopBar(QtWidgets.QFrame):
    """Combines search, status indicators and notifications."""

    request_search = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background: #0d1117; border-bottom: 1px solid #30363d;")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(16)

        self.search = SearchField()
        self.search.request_search.connect(self.request_search)
        layout.addWidget(self.search, 3)

        self.status = StatusIndicator()
        layout.addWidget(self.status, 2)

        self.notifications = NotificationCenter()
        layout.addWidget(self.notifications, 1)


class DashboardPage(QtWidgets.QWidget):
    """Dashboard showing active analyses, metrics and event log."""

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        cards = QtWidgets.QGridLayout()
        cards.setHorizontalSpacing(16)
        cards.setVerticalSpacing(16)

        cards.addWidget(self._metric_card("Активные проверки", "12", "🟢"), 0, 0)
        cards.addWidget(self._metric_card("Завершенные", "48", "✅"), 0, 1)
        cards.addWidget(self._metric_card("Высокий риск", "5", "🛑"), 0, 2)
        cards.addWidget(self._metric_card("Средний риск", "9", "⚠️"), 0, 3)
        layout.addLayout(cards)

        distribution = QtWidgets.QGroupBox("Распределение индекса риска")
        distribution.setLayout(QtWidgets.QVBoxLayout())
        distribution.layout().addWidget(self._risk_chart_placeholder())
        layout.addWidget(distribution)

        transactions = QtWidgets.QGroupBox("Последние транзакции")
        tx_layout = QtWidgets.QVBoxLayout()
        table = QtWidgets.QTableWidget(5, 4)
        table.setHorizontalHeaderLabels(["Адрес", "Сеть", "Сумма", "Статус"])
        for row in range(5):
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"0xABCD{row:02d}"))
            table.setItem(row, 1, QtWidgets.QTableWidgetItem("Ethereum"))
            table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{10 + row * 2:.2f} ETH"))
            table.setItem(row, 3, QtWidgets.QTableWidgetItem("На проверке"))
        table.horizontalHeader().setStretchLastSection(True)
        tx_layout.addWidget(table)
        transactions.setLayout(tx_layout)
        layout.addWidget(transactions)

        notifications = QtWidgets.QGroupBox("Центр уведомлений")
        notif_layout = QtWidgets.QVBoxLayout()
        log = QtWidgets.QListWidget()
        log.addItems(
            [
                "[WARN] Лимит API Chainz достигает 80%.",
                "[ERROR] Ошибка авторизации CoinGecko, требуется обновить токен.",
                "[INFO] Анализ 0xACF8 завершен успешно.",
            ]
        )
        notif_layout.addWidget(log)
        notifications.setLayout(notif_layout)
        layout.addWidget(notifications)

    def _metric_card(self, title: str, value: str, icon: str) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setStyleSheet(
            "background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 16px;"
        )
        layout = QtWidgets.QVBoxLayout(card)
        layout.addWidget(QtWidgets.QLabel(icon), alignment=QtCore.Qt.AlignRight)
        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet("color: #8b949e; font-size: 12px;")
        layout.addWidget(title_label)
        value_label = QtWidgets.QLabel(value)
        value_label.setStyleSheet("color: #ffffff; font-size: 24px; font-weight: 600;")
        layout.addWidget(value_label)
        return card

    def _risk_chart_placeholder(self) -> QtWidgets.QWidget:
        placeholder = QtWidgets.QLabel("Диаграмма распределения (пока в разработке)")
        placeholder.setAlignment(QtCore.Qt.AlignCenter)
        placeholder.setMinimumHeight(160)
        placeholder.setStyleSheet(
            "color: #8b949e; border: 2px dashed #30363d; border-radius: 12px; padding: 24px;"
        )
        return placeholder


class NewAnalysisPage(QtWidgets.QWidget):
    """Form to launch new address analysis tasks."""

    analysis_completed = QtCore.Signal(AddressAnalysisResult)

    def __init__(self) -> None:
        super().__init__()
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
        for network in Network:
            self.network_combo.addItem(network.name.title(), network)
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

        self.launch_button.setDisabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        selected_network = self.network_combo.currentData()
        if not isinstance(selected_network, Network):
            self._handle_error("Выберите поддерживаемую сеть для анализа.")
            return

        self.log_output.append("Подключение к бесплатному API BlockCypher…")

        try:
            result = await self._perform_analysis(address, selected_network)
        except UnsupportedNetworkError as exc:
            self._handle_error(str(exc))
            QtWidgets.QMessageBox.warning(self, "Сеть не поддерживается", str(exc))
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

        self.log_output.append("Анализ завершен. Результаты доступны на вкладке 'Анализы'.")
        self.analysis_completed.emit(result)

    async def _perform_analysis(self, address: str, network: Network) -> AddressAnalysisResult:
        self.log_output.append("Запрос истории транзакций…")
        explorer = BlockCypherExplorerClient(network)
        mixer_client = HeuristicMixerClient(watchlist=_DEFAULT_MIXER_WATCHLIST)
        analyzer = ArcheBlowAnalyzer(explorer_clients=[explorer], mixer_clients=[mixer_client])
        result = await analyzer.analyze(address, network)
        if not result.hops:
            self.log_output.append("API не вернуло транзакции — возможно, адрес новый или данные ограничены.")
        else:
            self.log_output.append(f"Получено транзакций: {len(result.hops)}")
        return result

    def _handle_error(self, message: str) -> None:
        self.log_output.append(message)
        self.progress.setVisible(False)
        self.launch_button.setEnabled(True)


class AnalysesPage(QtWidgets.QWidget):
    """List of analyses with filters."""

    open_details = QtCore.Signal(AddressAnalysisResult)

    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        filter_bar = QtWidgets.QHBoxLayout()
        self.status_filter = QtWidgets.QComboBox()
        self.status_filter.addItems(["Все", "На проверке", "Завершен", "Требует внимания"])
        filter_bar.addWidget(QtWidgets.QLabel("Статус:"))
        filter_bar.addWidget(self.status_filter)

        self.network_filter = QtWidgets.QComboBox()
        self.network_filter.addItem("Все сети")
        for network in Network:
            self.network_filter.addItem(network.name.title())
        filter_bar.addWidget(QtWidgets.QLabel("Сеть:"))
        filter_bar.addWidget(self.network_filter)
        filter_bar.addStretch(1)

        layout.addLayout(filter_bar)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "Адрес",
            "Сеть",
            "Риск",
            "Статус",
            "Последнее обновление",
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table)

        self._results: list[AddressAnalysisResult] = []

    def add_result(self, result: AddressAnalysisResult) -> None:
        self._results.append(result)
        row = self.table.rowCount()
        self.table.insertRow(row)

        risk_display, _ = _risk_to_display(result.risk_level)
        risk_percent = f"{int(round(result.risk_score * 100))}%"
        status = "Требует внимания" if result.risk_level in {"high", "critical"} else "Завершен"
        timestamp = QtCore.QDateTime.currentDateTime().toString("dd.MM.yyyy HH:mm")

        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(result.address))
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(result.network.name.title()))
        self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{risk_percent} / {risk_display}"))
        self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(status))
        self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(timestamp))

    def _open_selected(self) -> None:
        current = self.table.currentItem()
        if current:
            row = current.row()
            if 0 <= row < len(self._results):
                self.open_details.emit(self._results[row])


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

    def __init__(self) -> None:
        super().__init__()
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

    def set_analysis(self, analysis: AddressAnalysisResult) -> None:
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
        self.services_list.addItems(["BlockCypher API", "Heuristic Mixer Watchlist"])

        self.graph_widget.load_from_analysis(analysis)
        self._populate_transactions(analysis)

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

        return widget

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
            timestamp = QtCore.QDateTime.fromSecsSinceEpoch(hop.timestamp, QtCore.Qt.UTC).toLocalTime()

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

        table = QtWidgets.QTableWidget(4, 5)
        table.setHorizontalHeaderLabels([
            "Сервис",
            "Статус",
            "API ключ",
            "Лимит",
            "Действия",
        ])
        services = [
            ("Blockchair", "Активен", "****1234", "75%", "Обновить токен"),
            ("Chainz", "Ограничен", "****5678", "90%", "Проверить лимиты"),
            ("CoinGecko", "Ошибка", "****9012", "--", "Просмотр логов"),
            ("OFAC Watchlist", "Активен", "N/A", "--", "Обновить"),
        ]
        for row, service in enumerate(services):
            for column, value in enumerate(service):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

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

        self.top_bar = TopBar()
        self.top_bar.request_search.connect(self._handle_search)
        content_layout.addWidget(self.top_bar)

        self.pages = QtWidgets.QStackedWidget()
        self.dashboard_page = DashboardPage()
        self.new_analysis_page = NewAnalysisPage()
        self.analyses_page = AnalysesPage()
        self.detail_page = AnalysisDetailPage()
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
        self.analyses_page.add_result(analysis)
        risk_display, _ = _risk_to_display(analysis.risk_level)
        QtWidgets.QMessageBox.information(
            self,
            "Анализ завершен",
            (
                f"Анализ адреса {analysis.address} ({analysis.network.name.title()}) завершен.\n"
                f"Итоговый уровень риска: {risk_display}."
            ),
        )
        self.detail_page.set_analysis(analysis)
        self.navigation.set_active("analyses")
        self.pages.setCurrentWidget(self.analyses_page)

    def _open_analysis_details(self, analysis: AddressAnalysisResult) -> None:
        self.detail_page.set_analysis(analysis)
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
