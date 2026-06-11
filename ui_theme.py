from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QGraphicsDropShadowEffect, QLabel, QTableWidget, QWidget


ACCENTS = {
    "student": "#111111",
    "proctor": "#111111",
    "admin": "#0B0B0B",
}


def _accent(role):
    return ACCENTS.get(role, "#1F4B99")


def apply_theme(window, role="admin"):
    accent = _accent(role)
    font = QFont("Segoe UI", 10)
    window.setFont(font)
    window.setStyleSheet(
        f"""
        QMainWindow {{
            background-color: #F5F5F4;
            color: #121212;
        }}
        QWidget {{
            color: #121212;
            selection-background-color: {accent};
            selection-color: white;
        }}
        QFrame[card='true'],
        QGroupBox {{
            background: #FFFFFF;
            border: 1px solid #E5E5E5;
            border-radius: 18px;
        }}
        QGroupBox {{
            margin-top: 18px;
            padding-top: 10px;
            font-size: 14px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 16px;
            padding: 0 8px;
            color: #4B4B4B;
        }}
        QLabel[title='true'] {{
            font-size: 26px;
            font-weight: 700;
            color: #111111;
        }}
        QLabel[subtitle='true'] {{
            font-size: 12px;
            color: #6A6A6A;
        }}
        QLabel[pill='true'] {{
            padding: 8px 12px;
            border-radius: 999px;
            font-weight: 600;
        }}
        QLineEdit,
        QTextEdit,
        QPlainTextEdit,
        QSpinBox,
        QDoubleSpinBox,
        QComboBox {{
            background: #FCFCFC;
            border: 1px solid #D8D8D8;
            border-radius: 10px;
            padding: 10px 12px;
            font-size: 13px;
        }}
        QLineEdit:focus,
        QTextEdit:focus,
        QPlainTextEdit:focus,
        QSpinBox:focus,
        QDoubleSpinBox:focus,
        QComboBox:focus {{
            border: 1px solid #1F1F1F;
        }}
        QPushButton {{
            background: {accent};
            color: white;
            border: none;
            border-radius: 10px;
            padding: 11px 16px;
            font-size: 13px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: #2A2A2A;
        }}
        QPushButton:disabled {{
            background: #CFCFCF;
            color: #F7F7F7;
        }}
        QTabWidget::pane {{
            border: 1px solid #E5E5E5;
            border-radius: 18px;
            background: #FFFFFF;
            top: -1px;
        }}
        QTabBar::tab {{
            background: transparent;
            border: none;
            padding: 12px 18px;
            margin-right: 6px;
            color: #707070;
            font-weight: 600;
        }}
        QTabBar::tab:selected {{
            color: #111111;
            border-bottom: 3px solid #111111;
        }}
        QScrollArea {{
            border: none;
            background: transparent;
        }}
        QHeaderView::section {{
            background: #F2F2F2;
            color: #303030;
            padding: 10px 12px;
            border: none;
            border-right: 1px solid #E1E1E1;
            font-weight: 700;
        }}
        QTableWidget {{
            background: #FFFFFF;
            border: 1px solid #E5E5E5;
            border-radius: 12px;
            gridline-color: #EFEFEF;
            alternate-background-color: #FAFAFA;
            font-size: 13px;
        }}
        QTableWidget::item:selected {{
            background: #111111;
            color: white;
        }}
        QTableWidget::item {{
            padding: 8px;
        }}
        QTableCornerButton::section {{
            background: #F2F2F2;
            border: none;
        }}
        """
    )


def style_stat_label(label: QLabel, accent: str):
    label.setAlignment(label.alignment() or 0x0004)
    label.setStyleSheet(
        f"""
        QLabel {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #171717, stop:1 #2B2B2B);
            color: white;
            border-radius: 18px;
            border-left: 4px solid {accent};
            padding: 24px;
            font-size: 20px;
            font-weight: 700;
        }}
        """
    )


def style_status_pill(label: QLabel, level: str):
    palettes = {
        "success": ("#ECFDF3", "#027A48"),
        "error": ("#FEF3F2", "#B42318"),
        "warning": ("#FFFAEB", "#B54708"),
        "info": ("#EFF8FF", "#175CD3"),
    }
    background, foreground = palettes.get(level, palettes["info"])
    label.setProperty("pill", True)
    label.setStyleSheet(
        f"""
        QLabel {{
            background: {background};
            color: {foreground};
            border-radius: 999px;
            padding: 8px 12px;
            font-weight: 700;
        }}
        """
    )


def polish_table(table: QTableWidget):
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)


def set_page_margins(widget: QWidget, left=24, top=24, right=24, bottom=24):
    layout = widget.layout()
    if layout is not None:
        layout.setContentsMargins(left, top, right, bottom)
        layout.setSpacing(18)


def apply_soft_shadow(widget: QWidget, blur=28, offset_y=6, alpha=36):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, offset_y)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect