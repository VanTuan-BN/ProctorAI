from typing import Callable, Iterable, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui_theme import ACCENTS, apply_soft_shadow, style_status_pill


class AppToolbar(QFrame):
    def __init__(self, title: str, subtitle: str = "", role: str = "admin", parent: Optional[QWidget] = None):
        super().__init__(parent)
        accent = ACCENTS.get(role, "#1F4B99")
        self.setObjectName("AppToolbar")
        self.setProperty("card", True)
        apply_soft_shadow(self, blur=32, offset_y=8, alpha=28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        self.title_label = QLabel(title)
        self.title_label.setProperty("title", True)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setProperty("subtitle", True)
        self.subtitle_label.setWordWrap(True)
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.subtitle_label)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setSpacing(10)
        self.badge = QLabel("Operational")
        style_status_pill(self.badge, "info")
        self.badge.setStyleSheet(self.badge.styleSheet() + f"QLabel {{ border: 1px solid {accent}; }}")
        self.actions_layout.addWidget(self.badge)

        layout.addLayout(title_box, 1)
        layout.addLayout(self.actions_layout)

    def set_text(self, title: str, subtitle: str):
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)

    def add_widget(self, widget):
        self.actions_layout.insertWidget(max(0, self.actions_layout.count() - 1), widget)


class SidebarNav(QFrame):
    def __init__(self, title: str, subtitle: str, items: Iterable[str], role: str = "admin", parent: Optional[QWidget] = None):
        super().__init__(parent)
        accent = ACCENTS.get(role, "#1F4B99")
        self.setProperty("card", True)
        self.setFixedWidth(260)
        apply_soft_shadow(self, blur=34, offset_y=10, alpha=32)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(16)

        brand = QLabel(title)
        brand.setProperty("title", True)
        brand.setStyleSheet("font-size: 20px; font-weight: 800;")
        note = QLabel(subtitle)
        note.setProperty("subtitle", True)
        note.setWordWrap(True)

        self.nav = QListWidget()
        self.nav.setStyleSheet(
            f"""
            QListWidget {{
                background: transparent;
                border: none;
                outline: none;
                padding: 4px 0;
            }}
            QListWidget::item {{
                padding: 12px 14px;
                border-radius: 12px;
                margin: 4px 0;
                color: #444444;
            }}
            QListWidget::item:selected {{
                background: {accent};
                color: white;
            }}
            QListWidget::item:hover {{
                background: #F0F0F0;
            }}
            """
        )
        for label in items:
            QListWidgetItem(label, self.nav)
        if self.nav.count() > 0:
            self.nav.setCurrentRow(0)

        footer = QLabel("Enterprise workspace")
        footer.setProperty("subtitle", True)

        layout.addWidget(brand)
        layout.addWidget(note)
        layout.addWidget(self.nav, 1)
        layout.addWidget(footer)


class FilterBar(QFrame):
    def __init__(self, placeholder: str, primary_text: str, secondary_text: Optional[str] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("card", True)
        apply_soft_shadow(self, blur=20, offset_y=5, alpha=24)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(placeholder)

        self.primary_button = QPushButton(primary_text)
        self.secondary_button = QPushButton(secondary_text or "") if secondary_text else None
        if self.secondary_button is not None:
            self.secondary_button.setStyleSheet("background:#ECECEC; color:#222222; border:1px solid #D8D8D8;")

        layout.addWidget(self.search_input, 1)
        layout.addWidget(self.primary_button)
        if self.secondary_button is not None:
            layout.addWidget(self.secondary_button)


class EmptyState(QFrame):
    def __init__(self, title: str, subtitle: str, action_text: Optional[str] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("card", True)
        apply_soft_shadow(self, blur=24, offset_y=6, alpha=24)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 32, 28, 32)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignCenter)

        icon = QLabel("No data")
        icon.setStyleSheet("font-size: 18px; font-weight: 700; color:#3F3F3F;")
        heading = QLabel(title)
        heading.setProperty("title", True)
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        detail = QLabel(subtitle)
        detail.setProperty("subtitle", True)
        detail.setAlignment(Qt.AlignCenter)
        detail.setWordWrap(True)

        layout.addWidget(icon, 0, Qt.AlignCenter)
        layout.addWidget(heading, 0, Qt.AlignCenter)
        layout.addWidget(detail, 0, Qt.AlignCenter)

        self.action_button = None
        if action_text:
            self.action_button = QPushButton(action_text)
            layout.addWidget(self.action_button, 0, Qt.AlignCenter)


class EnterpriseDialog(QDialog):
    def __init__(self, title: str, message: str, detail: str = "", role: str = "admin", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(440, 220)
        self.setStyleSheet("QDialog { background:#FBFBFB; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 18)
        layout.setSpacing(12)

        header = AppToolbar(title, message, role=role)
        header.badge.hide()
        layout.addWidget(header)

        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setProperty("subtitle", True)
            layout.addWidget(detail_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def connect_sidebar_to_tabs(sidebar: SidebarNav, tab_widget, toolbar: Optional[AppToolbar] = None, section_descriptions: Optional[Iterable[str]] = None):
    descriptions = list(section_descriptions or [])

    def sync_from_sidebar(index: int):
        if index < 0:
            return
        tab_widget.setCurrentIndex(index)
        if toolbar is not None:
            subtitle = descriptions[index] if index < len(descriptions) else ""
            item = sidebar.nav.item(index)
            toolbar.set_text(item.text(), subtitle)

    def sync_from_tabs(index: int):
        if index >= 0:
            sidebar.nav.setCurrentRow(index)

    sidebar.nav.currentRowChanged.connect(sync_from_sidebar)
    tab_widget.currentChanged.connect(sync_from_tabs)
    sync_from_sidebar(sidebar.nav.currentRow())


def wire_filter_bar(filter_bar: FilterBar, on_primary: Callable[[], None], on_secondary: Optional[Callable[[], None]] = None):
    filter_bar.primary_button.clicked.connect(on_primary)
    filter_bar.search_input.returnPressed.connect(on_primary)
    if filter_bar.secondary_button is not None and on_secondary is not None:
        filter_bar.secondary_button.clicked.connect(on_secondary)