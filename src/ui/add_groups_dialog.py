from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QPushButton, QLabel, QHBoxLayout
)
from PySide6.QtCore import Qt


class AddGroupsDialog(QDialog):
    """批量添加群组对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📝 批量添加群组")
        self.resize(500, 400)
        
        layout = QVBoxLayout(self)
        
        # 说明文字
        info = QLabel("📋 请输入群组链接或用户名（每行一个）：\n✅ 支持格式：链接、@用户名")
        layout.addWidget(info)
        
        # 输入框
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "示例：\n"
            "https://t.me/groupname\n"
            "https://t.me/+AbCdEfGhIjK\n"
            "@groupusername"
        )
        self.text_edit.textChanged.connect(self._update_count)
        layout.addWidget(self.text_edit)
        
        # 行数统计
        self.lbl_count = QLabel("📊 已输入：0 行")
        layout.addWidget(self.lbl_count)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("确定")
        btn_cancel = QPushButton("取消")
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch(1)
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
    
    def _update_count(self):
        text = self.text_edit.toPlainText()
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        self.lbl_count.setText(f"📊 已输入：{len(lines)} 行")
    
    def get_groups(self) -> list[str]:
        """获取输入的群组列表（去重、去空）"""
        text = self.text_edit.toPlainText()
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        # 去重但保持顺序
        seen = set()
        result = []
        for line in lines:
            if line not in seen:
                seen.add(line)
                result.append(line)
        return result

