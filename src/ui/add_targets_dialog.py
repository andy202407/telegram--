from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
)


class AddTargetsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📝 批量添加发送对象")
        self.resize(500, 400)

        layout = QVBoxLayout(self)

        # 说明文本
        label = QLabel("📋 请输入发送对象，每行一个：\n✅ 支持格式：@用户名、+手机号")
        layout.addWidget(label)

        # 文本输入框
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("@username\n+1234567890\n123456789\n...")
        layout.addWidget(self.text_input)

        # 统计信息
        self.lbl_count = QLabel("📊 已输入：0 行")
        layout.addWidget(self.lbl_count)
        
        # 监听文本变化
        self.text_input.textChanged.connect(self._update_count)

        # 按钮
        btn_layout = QHBoxLayout()
        self.btn_ok = QPushButton("确定")
        self.btn_cancel = QPushButton("取消")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self.btn_ok)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def _update_count(self):
        text = self.text_input.toPlainText()
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        self.lbl_count.setText(f"📊 已输入：{len(lines)} 行")

    def get_targets(self) -> list[str]:
        """返回所有输入的目标（去重、去空）"""
        text = self.text_input.toPlainText()
        targets = []
        seen = set()
        for line in text.split('\n'):
            target = line.strip()
            if target and target not in seen:
                targets.append(target)
                seen.add(target)
        return targets

