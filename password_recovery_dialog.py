from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
)


class PasswordRecoveryDialog(QDialog):
    def __init__(self, role_label, account_label, account_value="", full_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Yêu cầu cấp lại mật khẩu")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        intro = QLabel(
            f"Gửi yêu cầu cấp lại mật khẩu cho tài khoản {role_label.lower()}. "
            "Hệ thống sẽ không hiển thị lại mật khẩu cũ mà chỉ hỗ trợ đặt mật khẩu tạm mới."
        )
        intro.setWordWrap(True)
        intro.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(intro)

        form = QFormLayout()
        self.inp_account_id = QLineEdit(account_value)
        self.inp_full_name = QLineEdit(full_name)
        self.inp_note = QTextEdit()
        self.inp_note.setPlaceholderText("Mô tả ngắn tình huống mất mật khẩu hoặc thời điểm cần hỗ trợ")
        self.inp_note.setFixedHeight(90)

        form.addRow(account_label + ":", self.inp_account_id)
        form.addRow("Họ tên:", self.inp_full_name)
        form.addRow("Ghi chú:", self.inp_note)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Gửi yêu cầu")
        buttons.button(QDialogButtonBox.Cancel).setText("Hủy")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_payload(self):
        return {
            "account_id": self.inp_account_id.text().strip(),
            "full_name": self.inp_full_name.text().strip(),
            "note": self.inp_note.toPlainText().strip(),
        }