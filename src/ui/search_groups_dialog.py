from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QPushButton, QLabel, QHBoxLayout, QSpinBox
)
from PySide6.QtCore import Qt


class SearchGroupsDialog(QDialog):
    """采集群组对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔍 采集群组")
        self.resize(500, 400)
        
        layout = QVBoxLayout(self)
        
        # 说明文字
        info = QLabel("📋 请输入搜索关键词（支持多个关键词）：\n✅ 格式：每行一个关键词，或用逗号分隔")
        layout.addWidget(info)
        
        # 输入框
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "示例：\n"
            "crypto trading\n"
            "bitcoin,ethereum,defi\n"
            "区块链,加密货币"
        )
        self.text_edit.textChanged.connect(self._update_count)
        layout.addWidget(self.text_edit)
        
        # 关键词统计
        self.lbl_count = QLabel("📊 关键词数：0 个")
        layout.addWidget(self.lbl_count)
        
        # 搜索数量限制
        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("🔢 每个关键词搜索数量："))
        self.spin_limit = QSpinBox()
        self.spin_limit.setRange(10, 1000)
        self.spin_limit.setValue(500)  # 默认500
        self.spin_limit.setSuffix(" 个")
        self.spin_limit.setToolTip("每个关键词最多搜索的群组数量")
        limit_row.addWidget(self.spin_limit)
        limit_row.addStretch(1)
        layout.addLayout(limit_row)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("开始搜索")
        btn_cancel = QPushButton("取消")
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch(1)
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
    
    def _update_count(self):
        keywords = self.get_keywords()
        self.lbl_count.setText(f"📊 关键词数：{len(keywords)} 个")
    
    def get_keywords(self) -> list[str]:
        """获取关键词列表（支持换行和逗号分隔，去重）"""
        text = self.text_edit.toPlainText()
        keywords = []
        
        # 先按行分割
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            # 每行再按逗号分割
            for keyword in line.split(','):
                keyword = keyword.strip()
                if keyword and keyword not in keywords:
                    keywords.append(keyword)
        
        return keywords
    
    def get_search_limit(self) -> int:
        """获取搜索数量限制"""
        return self.spin_limit.value()

