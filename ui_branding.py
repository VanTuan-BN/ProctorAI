import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QLabel


_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_DIR = os.path.join(_ROOT_DIR, "assets", "logos")
LOGIN_LOGO_CANDIDATES = [
    "s-monitor-login.png",
    "s-monitor-login.jpg",
    "s-monitor-login.jpeg",
    "s-monitor-login.svg",
    "s-monitor-login-default.svg",
]
ROLE_LOGO_WIDTHS = {
    "student": 360,
    "proctor": 300,
    "admin": 280,
}
ROLE_LOGO_HEIGHTS = {
    "student": 200,
    "proctor": 170,
    "admin": 160,
}


def _resolve_login_logo_path():
    for file_name in LOGIN_LOGO_CANDIDATES:
        candidate_path = os.path.join(LOGO_DIR, file_name)
        if os.path.exists(candidate_path):
            return candidate_path
    return ""


def build_login_logo_label(role="student", max_width=None):
    label = QLabel()
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet("background:transparent; color:#0B4D67; font-weight:bold;")
    role_key = str(role or "student").lower()
    target_width = int(max_width or ROLE_LOGO_WIDTHS.get(role_key, 320))
    target_height = int(ROLE_LOGO_HEIGHTS.get(role_key, 170))
    label.setMinimumHeight(target_height)

    pixmap = QPixmap(_resolve_login_logo_path())
    if not pixmap.isNull():
        label.setPixmap(pixmap.scaled(target_width, target_height, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    else:
        label.setText("S-MONITOR")
    return label