from __future__ import annotations

from typing import Dict, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QPushButton,
)


class SettingsDialog(QDialog):
    def __init__(self, parent=None, initial: Dict[str, Any] | None = None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 发送配置")
        self.resize(420, 280)

        data = initial or {}
        self.layout = QVBoxLayout(self)

        # 一行一个设置
        row1 = QHBoxLayout()
        self.radio_random = QRadioButton("⏱️ 随机间隔(秒)")
        self.radio_fixed = QRadioButton("⏰ 固定间隔(秒)")
        self.radio_random.setChecked(bool(data.get("random", True)))
        self.radio_fixed.setChecked(not bool(data.get("random", True)))
        row1.addWidget(self.radio_random)
        row1.addWidget(self.radio_fixed)
        row1.addStretch(1)
        self.layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("📉 最小间隔："))
        self.spin_min = QSpinBox(); self.spin_min.setRange(1, 600); self.spin_min.setValue(int(data.get("min", 15)))
        row2.addWidget(self.spin_min)
        row2.addWidget(QLabel("📈 最大间隔："))
        self.spin_max = QSpinBox(); self.spin_max.setRange(1, 600); self.spin_max.setValue(int(data.get("max", 15)))
        row2.addWidget(self.spin_max)
        row2.addStretch(1)
        self.layout.addLayout(row2)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("⏰ 固定间隔："))
        self.spin_fixed = QSpinBox(); self.spin_fixed.setRange(1, 600); self.spin_fixed.setValue(int(data.get("fixed", 15)))
        row4.addWidget(self.spin_fixed)
        self.layout.addLayout(row4)

        row5 = QHBoxLayout()
        row5.addWidget(QLabel("📊 单账号发送上限："))
        self.spin_per = QSpinBox(); self.spin_per.setRange(1, 1000); self.spin_per.setValue(int(data.get("per", 20)))
        row5.addWidget(self.spin_per)
        self.layout.addLayout(row5)

        row6 = QHBoxLayout()
        row6.addWidget(QLabel("🔢 并发账号数量："))
        self.spin_conc = QSpinBox(); self.spin_conc.setRange(1, 50); self.spin_conc.setValue(int(data.get("conc", 6)))
        row6.addWidget(self.spin_conc)
        self.layout.addLayout(row6)

        row7 = QHBoxLayout()
        row7.addWidget(QLabel("📅 每日发送上限："))
        self.spin_daily_limit = QSpinBox(); self.spin_daily_limit.setRange(0, 10000); self.spin_daily_limit.setValue(int(data.get("daily_limit", 0)))
        self.spin_daily_limit.setSuffix(" 条/天")
        self.spin_daily_limit.setToolTip("0表示无限制，每个账号每天最多发送数量")
        row7.addWidget(self.spin_daily_limit)
        self.layout.addLayout(row7)

        btns = QHBoxLayout()
        self.btn_ok = QPushButton("确定")
        self.btn_cancel = QPushButton("取消")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)
        self.layout.addLayout(btns)

    def get_settings(self) -> Dict[str, Any]:
        return {
            "random": self.radio_random.isChecked(),
            "min": int(self.spin_min.value()),
            "max": int(self.spin_max.value()),
            "fixed": int(self.spin_fixed.value()),
            "per": int(self.spin_per.value()),
            "conc": int(self.spin_conc.value()),
            "daily_limit": int(self.spin_daily_limit.value()),
        }


