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
from dataclasses import dataclass
from typing import Iterable

from PySide6 import QtCore, QtGui, QtWidgets
from qasync import QEventLoop, asyncSlot

from archeblow_service import Network


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

    start_analysis = QtCore.Signal(str, Network)

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
        self.log_output.append("Запуск анализа…")

        # Placeholder for asynchronous integration.  Sleeps simulate workflow.
        await asyncio.sleep(0.5)
        self.log_output.append("Получение транзакций из блокчейна…")
        await asyncio.sleep(0.5)
        self.log_output.append("Детектирование миксеров…")
        await asyncio.sleep(0.5)
        self.log_output.append("Вычисление индекса риска…")
        await asyncio.sleep(0.3)

        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.launch_button.setEnabled(True)
        self.log_output.append("Анализ завершен. Результаты доступны на вкладке 'Анализы'.")

        selected_network = self.network_combo.currentData()
        if isinstance(selected_network, Network):
            self.start_analysis.emit(address, selected_network)


class AnalysesPage(QtWidgets.QWidget):
    """List of analyses with filters."""

    open_details = QtCore.Signal(str)

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

        self.table = QtWidgets.QTableWidget(8, 5)
        self.table.setHorizontalHeaderLabels([
            "Адрес",
            "Сеть",
            "Риск",
            "Статус",
            "Последнее обновление",
        ])
        sample_status = ["На проверке", "Завершен", "Требует внимания"]
        for row in range(self.table.rowCount()):
            address = f"0xDEMO{row:04d}"
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(address))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem("Ethereum"))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{35 + row * 3}%"))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(sample_status[row % len(sample_status)]))
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem("10 минут назад"))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.table)

    def _open_selected(self) -> None:
        current = self.table.currentItem()
        if current:
            address_item = self.table.item(current.row(), 0)
            if address_item:
                self.open_details.emit(address_item.text())


class GraphPlaceholder(QtWidgets.QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumHeight(240)
        self.setStyleSheet("color: #8b949e; border: 2px dashed #30363d; border-radius: 12px; padding: 24px;")


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
        self.tabs.addTab(self._create_overview(), "Обзор")
        self.tabs.addTab(GraphPlaceholder("Граф связей появится здесь"), "Граф")
        self.tabs.addTab(self._create_transactions_tab(), "Транзакции")
        self.tabs.addTab(GraphPlaceholder("Прогнозные модели в разработке"), "Прогнозы")
        self.tabs.addTab(self._create_report_tab(), "Отчет")
        layout.addWidget(self.tabs)

    def set_address(self, address: str, network: str) -> None:
        self.header.setText(f"Адрес: {address} | Сеть: {network} | Обновлено: только что")

    def _create_overview(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setSpacing(12)

        risk_box = QtWidgets.QGroupBox("Индекс риска")
        risk_layout = QtWidgets.QVBoxLayout()
        risk_progress = QtWidgets.QProgressBar()
        risk_progress.setRange(0, 100)
        risk_progress.setValue(68)
        risk_progress.setFormat("68% (Высокий риск)")
        risk_layout.addWidget(risk_progress)
        risk_notes = QtWidgets.QTextEdit()
        risk_notes.setReadOnly(True)
        risk_notes.setPlainText(
            "\n".join(
                [
                    "- Обнаружены совпадения с миксерами.",
                    "- Высокая скорость перемещения средств.",
                    "- Необычные кластеры транзакций за последние 48 часов.",
                ]
            )
        )
        risk_layout.addWidget(risk_notes)
        risk_box.setLayout(risk_layout)
        layout.addWidget(risk_box)

        services_box = QtWidgets.QGroupBox("Задействованные сервисы")
        services_layout = QtWidgets.QVBoxLayout()
        services_list = QtWidgets.QListWidget()
        services_list.addItems(
            [
                "Blockchair API",
                "Chainz Public",
                "CoinGecko Market Data",
                "OFAC Watchlist",
            ]
        )
        services_layout.addWidget(services_list)
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
        status_filter.addItems(["Все", "Подозрительные", "Подтвержденные"])
        filter_bar.addWidget(QtWidgets.QLabel("Дата с:"))
        filter_bar.addWidget(date_filter)
        filter_bar.addWidget(QtWidgets.QLabel("Сумма:"))
        filter_bar.addWidget(amount_filter)
        filter_bar.addWidget(QtWidgets.QLabel("Статус:"))
        filter_bar.addWidget(status_filter)
        filter_bar.addStretch(1)
        layout.addLayout(filter_bar)

        table = QtWidgets.QTableWidget(6, 5)
        table.setHorizontalHeaderLabels([
            "TX Hash",
            "От",
            "К",
            "Сумма",
            "Флаг",
        ])
        for row in range(table.rowCount()):
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"0xHASH{row:04d}"))
            table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"0xSRC{row:04d}"))
            table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"0xDST{row:04d}"))
            table.setItem(row, 3, QtWidgets.QTableWidgetItem(f"{1.5 + row * 0.3:.2f} BTC"))
            table.setItem(row, 4, QtWidgets.QTableWidgetItem("Подозрительная"))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

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

        self.new_analysis_page.start_analysis.connect(self._analysis_started)
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

    def _analysis_started(self, address: str, network: Network) -> None:
        # Automatically switch to the analyses list after launching a task.
        self.navigation.set_active("analyses")
        self.pages.setCurrentWidget(self.analyses_page)
        # In a real application the analyses table would refresh with a new row.
        QtWidgets.QMessageBox.information(
            self,
            "Анализ запущен",
            f"Анализ адреса {address} в сети {network.name.title()} запущен.",
        )

    def _open_analysis_details(self, address: str) -> None:
        self.detail_page.set_address(address, "Ethereum")
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
