from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QPushButton, QLabel, QHBoxLayout,
    QLineEdit, QSpinBox, QDoubleSpinBox
)


class BotSearchDialog(QDialog):
    """机器人采集对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🤖 机器人采集（通过搜索机器人）")
        self.resize(520, 520)

        layout = QVBoxLayout(self)

        # 说明
        layout.addWidget(QLabel("请输入搜索关键词（每行一个或用逗号分隔）"))

        # 关键词输入
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("例如：\n东南亚, 体育\ncrypto group\nbitcoin chat")
        layout.addWidget(self.text_edit)

        # 统计
        self.lbl_count = QLabel("关键词数：0 个")
        self.text_edit.textChanged.connect(self._update_count)
        layout.addWidget(self.lbl_count)

        # Bot 用户名
        row_bot = QHBoxLayout()
        row_bot.addWidget(QLabel("机器人用户名："))
        self.edit_bot = QLineEdit("@soso")
        self.edit_bot.setPlaceholderText("例如：@soso")
        row_bot.addWidget(self.edit_bot)
        row_bot.addStretch(1)
        layout.addLayout(row_bot)

        # 每个关键词最大页数
        row_pages = QHBoxLayout()
        row_pages.addWidget(QLabel("每个关键词最大页数："))
        self.spin_pages = QSpinBox()
        self.spin_pages.setRange(1, 1000)
        self.spin_pages.setValue(50)
        row_pages.addWidget(self.spin_pages)
        row_pages.addStretch(1)
        layout.addLayout(row_pages)

        # 翻页间隔（秒）
        row_delay = QHBoxLayout()
        row_delay.addWidget(QLabel("翻页间隔（秒）："))
        self.spin_delay = QDoubleSpinBox()
        self.spin_delay.setRange(0.3, 10.0)
        self.spin_delay.setSingleStep(0.1)
        self.spin_delay.setValue(1.2)
        row_delay.addWidget(self.spin_delay)
        row_delay.addStretch(1)
        layout.addLayout(row_delay)

        # 按钮
        btns = QHBoxLayout()
        btn_ok = QPushButton("开始采集")
        btn_cancel = QPushButton("取消")
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        layout.addLayout(btns)

    def _update_count(self):
        self.lbl_count.setText(f"关键词数：{len(self.get_keywords())} 个")

    def get_keywords(self) -> list[str]:
        text = self.text_edit.toPlainText()
        items: list[str] = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            for part in line.split(','):
                kw = part.strip()
                if kw and kw not in items:
                    items.append(kw)
        return items

    def get_bot_username(self) -> str:
        v = (self.edit_bot.text() or "").strip()
        if v and not v.startswith('@'):
            v = '@' + v
        return v or '@soso'

    def get_max_pages(self) -> int:
        return int(self.spin_pages.value())

    def get_delay(self) -> float:
        return float(self.spin_delay.value())


