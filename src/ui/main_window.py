from __future__ import annotations

from pathlib import Path
from typing import List
import threading
import asyncio

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QIcon, QColor, QBrush, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QTableWidget,
    QTableWidgetItem,
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QTextEdit,
    QGroupBox,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QProgressBar,
    QApplication,
)

from ..db.repo import Repo
from ..db.models import Account
from ..core.member_fetcher import fetch_members_into_db
from ..core.sender import SenderEngine
from ..broadcast import send_messages, DEFAULT_RECIPIENTS_FILE
from .settings_dialog import SettingsDialog
from .add_targets_dialog import AddTargetsDialog
from .search_groups_dialog import SearchGroupsDialog
from .bot_search_dialog import BotSearchDialog


class WorkerSignals(QObject):
    """工作线程信号类"""
    log_message = Signal(str)
    progress_update = Signal(dict)
    cleanup_ui = Signal()


class MainWindow(QMainWindow):
    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo
        self.setWindowTitle("Telegram 群发助手")
        self.resize(1100, 700)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Tabs
        self.tab_actions = QWidget()
        self.tab_accounts = QWidget()
        self.tab_targets = QWidget()
        self.tab_groups = QWidget()
        self.tab_help = QWidget()

        self.tabs.addTab(self.tab_actions, "📊 工作台")
        self.tabs.addTab(self.tab_accounts, "👤 账号管理")
        self.tabs.addTab(self.tab_targets, "🎯 发送对象")
        self.tabs.addTab(self.tab_groups, "👥 群组管理")
        self.tabs.addTab(self.tab_help, "📖 使用说明")

        self._setup_actions()
        self._setup_accounts()
        self._setup_targets()
        self._setup_groups()
        self._setup_help()

        # Load or default settings (必须在 refresh 之前)
        try:
            self._settings = self.repo.load_setting("send_config", {"random": True, "min": 15, "max": 15, "fixed": 15, "per": 2, "conc": 2, "daily_limit": 0})
        except Exception:
            # 如果数据库表不存在，使用默认设置
            self._settings = {"random": True, "min": 15, "max": 15, "fixed": 15, "per": 2, "conc": 2, "daily_limit": 0}
        
        self._update_settings_summary()
        self._sender_engine = None

        # 加载项目根目录设置
        self._load_project_root()
        
        # 尝试刷新，如果失败则跳过
        try:
            self.refresh()
        except Exception:
            # 如果数据库表不存在，跳过刷新
            pass
        
        self.tabs.setCurrentIndex(0)
        
        # 程序完全启动后，延迟检查是否需要显示启动提示
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1000, self._check_startup_tips)  # 1秒后显示
        
        # 设置复制快捷键
        self._setup_copy_shortcuts()

    def _setup_copy_shortcuts(self):
        """设置复制快捷键"""
        # 为所有表格设置Ctrl+C复制功能
        tables = [self.table_accounts, self.table_targets, self.table_groups]
        for table in tables:
            copy_shortcut = QShortcut(QKeySequence.Copy, table)
            copy_shortcut.activated.connect(lambda t=table: self._copy_table_selection(t))

    def _copy_table_selection(self, table: QTableWidget):
        """复制表格选中内容到剪贴板"""
        selection = table.selectedRanges()
        if not selection:
            return
        
        # 获取选中的内容
        copied_text = ""
        for range_obj in selection:
            for row in range(range_obj.topRow(), range_obj.bottomRow() + 1):
                row_data = []
                for col in range(range_obj.leftColumn(), range_obj.rightColumn() + 1):
                    item = table.item(row, col)
                    if item:
                        row_data.append(item.text())
                    else:
                        row_data.append("")
                copied_text += "\t".join(row_data) + "\n"
        
        # 复制到剪贴板
        if copied_text:
            clipboard = QApplication.clipboard()
            clipboard.setText(copied_text.strip())
            self._append_log(f"📋 已复制 {len(selection)} 个选中区域到剪贴板")

    def _load_project_root(self):
        """加载项目根目录设置"""
        try:
            # 先从内存中读取，再从数据库中读取
            saved_root = None
            if hasattr(self, '_temp_settings') and "project_root" in self._temp_settings:
                saved_root = self._temp_settings["project_root"]
                self._append_log(f"📁 从内存加载项目根目录: {saved_root}")
            else:
                saved_root = self.repo.load_setting("project_root", "")
                if saved_root:
                    self._append_log(f"📁 从数据库加载项目根目录: {saved_root}")
            
            if saved_root:
                self.edit_project_root.setText(saved_root)
                from ..utils import PathManager
                PathManager.set_root(saved_root)
            else:
                # 提示用户手动配置（使用固定文本）
                self.edit_project_root.setPlaceholderText("请输入项目根目录路径（例如：C:\\MyApp）")
                self._append_log(f"⚠️ 请先设置项目根目录，然后点击初始化数据")
        except Exception as e:
            self.edit_project_root.setPlaceholderText("请输入项目根目录路径（例如：C:\\MyApp）")
            self._append_log(f"⚠️ 请先设置项目根目录，然后点击初始化数据")

    def _save_project_root(self):
        """保存项目根目录设置"""
        try:
            root_path = self.edit_project_root.text().strip()
            if not root_path:
                self._show_message("请输入项目根目录路径")
                return
            
            # 验证路径是否存在
            from pathlib import Path
            path_obj = Path(root_path)
            if not path_obj.exists():
                self._show_message("指定的路径不存在，请检查路径是否正确")
                return
            
            if not path_obj.is_dir():
                self._show_message("指定的路径不是目录，请选择正确的目录")
                return
            
            # 数据库未初始化，保存到内存
            if not hasattr(self, '_temp_settings'):
                self._temp_settings = {}
            self._temp_settings["project_root"] = root_path
            self._append_log(f"✅ 项目根目录已保存到内存: {root_path}")
            
            # 更新全局路径管理器
            from ..utils import PathManager
            PathManager.set_root(root_path)
            
            # 更新初始化按钮状态
            self._update_init_button_state()
            
            self._show_message("项目根目录保存成功！")
            
        except Exception as e:
            self._append_log(f"❌ 保存项目根目录失败: {e}")
            self._show_message(f"保存失败: {e}")

    def _update_init_button_state(self):
        """更新初始化按钮状态"""
        try:
            is_initialized = self.repo.load_setting("data_initialized", False)
            if is_initialized:
                self.btn_init_data.setText("🔄 重新初始化")
                self.btn_init_data.setEnabled(True)
                self.btn_init_data.setStyleSheet("QPushButton { background-color: #ffa500; color: white; }")
                self.btn_init_data.setToolTip("重新初始化数据库和目录结构（会保留现有数据）")
            else:
                self.btn_init_data.setText("🔧 初始化数据")
                self.btn_init_data.setEnabled(True)
                self.btn_init_data.setStyleSheet("")
                self.btn_init_data.setToolTip("手动初始化数据库和目录结构")
        except Exception:
            # 如果数据库表不存在，设置为未初始化状态
            self.btn_init_data.setText("🔧 初始化数据")
            self.btn_init_data.setEnabled(True)
            self.btn_init_data.setStyleSheet("")
            self.btn_init_data.setToolTip("手动初始化数据库和目录结构")

    def _init_data(self):
        """手动初始化数据"""
        try:
            self._append_log("🔧 开始初始化数据...")
            
            self._append_log("📦 正在导入模块...")
            from ..utils import PathManager, ensure_directories, get_db_path
            from ..core.syncer import run_startup_sync, run_startup_account_check
            from ..db.models import Base, create_session
            from sqlalchemy import create_engine
            from PySide6.QtWidgets import QMessageBox
            self._append_log("📦 模块导入完成")
            
            # 1. 验证项目根目录
            self._append_log("🔍 开始验证项目根目录...")
            root_path = self.edit_project_root.text().strip()
            if not root_path:
                self._show_message("请先设置并保存项目根目录")
                return
            
            self._append_log(f"🔍 输入框中的路径: '{root_path}' (长度: {len(root_path)})")
            
            # 先从内存中读取，再从数据库中读取
            saved_root = None
            if hasattr(self, '_temp_settings') and "project_root" in self._temp_settings:
                saved_root = self._temp_settings["project_root"]
                self._append_log(f"🔍 从内存读取的路径: '{saved_root}' (长度: {len(saved_root)})")
            else:
                saved_root = self.repo.load_setting("project_root", "")
                self._append_log(f"🔍 从数据库读取的路径: '{saved_root}' (长度: {len(saved_root)})")
            
            if saved_root != root_path:
                self._show_message("项目根目录已修改但未保存，请先点击'保存'按钮")
                return
            
            self._append_log("🔍 路径验证通过，开始检查路径存在性...")
            
            # 验证路径是否存在和可访问
            from pathlib import Path
            path_obj = Path(root_path)
            if not path_obj.exists():
                self._append_log(f"❌ 项目根目录不存在: {root_path}")
                self._show_message(f"项目根目录不存在: {root_path}")
                return
            
            if not path_obj.is_dir():
                self._append_log(f"❌ 项目根目录不是目录: {root_path}")
                self._show_message(f"项目根目录不是目录: {root_path}")
                return
            
            # 测试写入权限
            try:
                test_file = path_obj / "test_write_permission.tmp"
                test_file.write_text("test")
                test_file.unlink()
            except Exception as e:
                self._append_log(f"❌ 项目根目录无写入权限: {root_path}, 错误: {e}")
                self._show_message(f"项目根目录无写入权限: {root_path}")
                return
            
            self._append_log(f"📁 使用项目根目录: {root_path}")
            PathManager.set_root(root_path)
            
            # 2. 判断是首次还是重新初始化
            is_initialized = self.repo.load_setting("data_initialized", False)
            self._append_log(f"🔍 初始化状态: {'已初始化' if is_initialized else '未初始化'}")
            
            if not is_initialized:
                # 首次初始化
                self._first_time_init()
            else:
                # 重新初始化（同步数据）
                self._reinitialize_data()
            
        except Exception as e:
            self._append_log(f"❌ 数据初始化失败: {e}")
            import traceback
            traceback.print_exc()
            self._show_message(f"初始化失败: {e}")

    def _first_time_init(self):
        """首次初始化"""
        try:
            from ..utils import ensure_directories, get_db_path
            from ..core.syncer import run_startup_sync, run_startup_account_check
            from ..db.models import Base, create_session
            from sqlalchemy import create_engine
            
            self._append_log("🔧 开始首次初始化...")
            
            # 1. 创建目录结构
            self._append_log("📁 正在创建目录结构...")
            ensure_directories()
            self._append_log("📁 目录结构已创建")
            
            # 2. 创建数据库
            self._append_log("🗄️ 正在创建数据库...")
            db_path = get_db_path()
            self._append_log(f"🗄️ 数据库路径: {db_path}")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.repo.Session = create_session(db_path, create_dirs=True)
            self._append_log("🗄️ 数据库连接已创建")
            
            # 3. 创建表结构
            self._append_log("🗄️ 正在创建表结构...")
            engine = create_engine(f"sqlite:///{db_path}")
            Base.metadata.create_all(engine)
            self._append_log("🗄️ 数据库表结构已创建")
            
            # 4. 运行迁移
            self._append_log("🔧 正在运行数据库迁移...")
            from migrate_db import migrate_database
            migrate_database()
            self._append_log("🔧 数据库迁移完成")
            
            # 5. 同步数据
            self._append_log("📥 正在同步数据...")
            result = run_startup_sync(self.repo)
            self._append_log(f"📥 数据同步完成：账号 {result['accounts_new']} 个，目标 {result['targets_new']} 个，群组 {result['groups_new']} 个")
            
            # 6. 检测账号状态
            self._append_log("🔍 正在检测账号状态...")
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            totals = loop.run_until_complete(run_startup_account_check(self.repo))
            loop.close()
            self._append_log(f"🔍 账号状态检测完成：正常 {totals.get('ok', 0)} 个，异常 {totals.get('error', 0)} 个，未授权 {totals.get('unauthorized', 0)} 个")
            
            # 7. 标记已初始化
            self.repo.save_setting("data_initialized", True)
            
            # 8. 将内存中的设置迁移到数据库
            if hasattr(self, '_temp_settings'):
                for key, value in self._temp_settings.items():
                    self.repo.save_setting(key, value)
                    self._append_log(f"📦 设置已迁移到数据库: {key} = {value}")
                # 清空内存设置
                delattr(self, '_temp_settings')
            
            self._append_log("✅ 首次初始化完成！")
            self._update_init_button_state()
            self.refresh()
            
        except Exception as e:
            self._append_log(f"❌ 首次初始化失败: {e}")
            import traceback
            traceback.print_exc()
            raise

    def _reinitialize_data(self):
        """重新初始化（同步数据）"""
        from PySide6.QtWidgets import QMessageBox
        from ..core.syncer import read_accounts_from_files, read_targets_from_file, read_groups_from_file
        from sqlalchemy import select
        from ..db.models import Account, Target, Group
        
        # 询问确认
        reply = QMessageBox.question(
            self, "确认重新初始化",
            "重新初始化会同步以下数据：\n\n"
            "• 账号：新增文件中的新账号，删除文件中已不存在的账号\n"
            "• 目标：新增文件中的新目标（不删除已有目标）\n"
            "• 群组：新增文件中的新群组（不删除已有群组）\n\n"
            "是否继续？",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        self._append_log("🔄 开始重新初始化（数据同步）...")
        
        # 1. 同步账号（新增+删除）
        file_accounts = read_accounts_from_files()
        file_phones = {acc['phone'] for acc in file_accounts}
        
        with self.repo.session() as s:
            db_accounts = s.execute(select(Account)).scalars().all()
            db_phones = {acc.phone for acc in db_accounts}
            
            # 删除不存在的
            to_delete = db_phones - file_phones
            for phone in to_delete:
                acc = s.execute(select(Account).where(Account.phone == phone)).scalar_one()
                s.delete(acc)
                self._append_log(f"🗑️ 删除账号: {phone}")
            
            s.commit()
        
        # 新增的账号
        new_count = self.repo.upsert_accounts(file_accounts)
        self._append_log(f"📥 新增账号: {new_count} 个")
        
        # 2. 同步目标（仅新增）
        file_targets = read_targets_from_file()
        new_targets = self.repo.upsert_targets(file_targets, source="file")
        self._append_log(f"📥 新增目标: {new_targets} 个")
        
        # 3. 同步群组（仅新增）
        file_groups = read_groups_from_file()
        new_groups = self.repo.upsert_groups(file_groups)
        self._append_log(f"📥 新增群组: {new_groups} 个")
        
        # 4. 检测账号状态
        import asyncio
        from ..core.syncer import run_startup_account_check
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        totals = loop.run_until_complete(run_startup_account_check(self.repo))
        loop.close()
        
        self._append_log("✅ 重新初始化完成！")
        self.refresh()

    def _run_database_migrations(self):
        """执行数据库迁移"""
        try:
            # 检查并添加send_status字段
            self.repo._ensure_send_status_field()
            self._append_log("🔧 数据库迁移完成")
        except Exception as e:
            self._append_log(f"⚠️ 数据库迁移失败: {e}")
            # 迁移失败不影响初始化流程

    def _check_startup_tips(self):
        """检查并显示启动提示"""
        try:
            # 检查是否已经显示过提示
            tips_shown = self.repo.load_setting("startup_tips_shown", False)
            if tips_shown:
                return
            
            # 检查项目根目录是否已设置
            project_root = self.repo.load_setting("project_root", "")
            if not project_root:
                self._show_startup_tips()
        except Exception:
            # 如果数据库表不存在，显示提示
            self._show_startup_tips()

    def _show_startup_tips(self):
        """显示启动提示"""
        from PySide6.QtWidgets import QMessageBox, QCheckBox
        
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("📋 使用提示")
        msg.setText("🎯 首次使用指南")
        msg.setInformativeText(
            "📁 请手动设置项目根目录：\n\n"
            "1️⃣ 在顶部输入框中输入你的项目根目录路径\n"
            "2️⃣ 点击 '💾 保存' 按钮保存设置\n"
            "3️⃣ 点击 '🔧 初始化数据' 按钮初始化数据库\n\n"
            "💡 提示：项目根目录应该包含以下文件夹：\n"
            "   • 协议号/ - 存放账号文件\n"
            "   • 群发目标/ - 存放目标用户文件\n"
            "   • 群/ - 存放群组文件\n"
            "   • data/ - 存放数据库文件"
        )
        
        # 添加"不再显示"复选框
        checkbox = QCheckBox("下次启动不再显示此提示")
        msg.setCheckBox(checkbox)
        
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec()
        
        # 根据复选框状态决定是否保存设置
        if checkbox.isChecked():
            # 用户选择不再显示，保存设置
            self.repo.save_setting("startup_tips_shown", True)
            self._append_log("📋 已设置不再显示启动提示")
        else:
            # 用户没有选择不再显示，下次还会显示
            pass

    def _setup_actions(self):
        layout = QVBoxLayout(self.tab_actions)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # 账号选择和项目根目录
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("📱 当前账号："))
        self.combo_account = QComboBox()
        top_row.addWidget(self.combo_account)
        
        self.btn_refresh = QPushButton("🔄 刷新数据")
        self.btn_refresh.clicked.connect(self.refresh)
        top_row.addWidget(self.btn_refresh)
        
        # 项目根目录设置
        top_row.addWidget(QLabel("📁 项目根目录："))
        self.edit_project_root = QLineEdit()
        self.edit_project_root.setPlaceholderText("请手动输入项目根目录路径...")
        self.edit_project_root.setMinimumWidth(200)
        self.edit_project_root.setToolTip("设置项目根目录，所有相对路径将基于此目录")
        top_row.addWidget(self.edit_project_root)
        
        self.btn_save_root = QPushButton("💾 保存")
        self.btn_save_root.setToolTip("保存项目根目录设置")
        self.btn_save_root.clicked.connect(self._save_project_root)
        top_row.addWidget(self.btn_save_root)
        
        # 手动初始化数据按钮
        self.btn_init_data = QPushButton("🔧 初始化数据")
        self.btn_init_data.setToolTip("手动初始化数据库和目录结构")
        self.btn_init_data.clicked.connect(self._init_data)
        top_row.addWidget(self.btn_init_data)
        
        # 设置按钮放在群发卡片中，此处仅保留刷新
        top_row.addStretch(1)
        layout.addLayout(top_row)

        # 左右两列控制区容器（左侧更窄，右侧更宽）
        controls_container = QWidget()
        grid = QGridLayout(controls_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # 采集功能（左列）
        grp_fetch = QGroupBox("📥 采集功能")
        fetch_layout = QVBoxLayout(grp_fetch)
        
        # 采集群组按钮（API）- 已注释
        # self.btn_search_groups = QPushButton("🔍 采集群组（API）")
        # self.btn_search_groups.setMinimumWidth(120)
        # self.btn_search_groups.clicked.connect(self._on_search_groups)
        # self.btn_search_groups.setToolTip("根据关键词搜索公开群组并保存")
        # try:
        #     self.btn_search_groups.setIcon(self.style().standardIcon(self.style().SP_FileDialogContentsView))
        # except Exception:
        #     pass
        # fetch_layout.addWidget(self.btn_search_groups)
        
        # 机器人采集按钮 - 已注释
        # self.btn_bot_collect = QPushButton("🤖 机器人采集")
        # self.btn_bot_collect.setMinimumWidth(120)
        # self.btn_bot_collect.setToolTip("通过搜索机器人（如 @soso）采集群组")
        # self.btn_bot_collect.clicked.connect(self._on_bot_collect_groups)
        # fetch_layout.addWidget(self.btn_bot_collect)
        
        # 采集模式选择
        fetch_mode_label = QLabel("📋 采集模式：")
        fetch_layout.addWidget(fetch_mode_label)
        
        self.radio_fetch_all = QRadioButton("全部成员")
        self.radio_fetch_all.setToolTip("采集所有群成员")
        fetch_layout.addWidget(self.radio_fetch_all)
        
        self.radio_fetch_online = QRadioButton("在线成员")
        self.radio_fetch_online.setToolTip("只采集当前在线的成员")
        fetch_layout.addWidget(self.radio_fetch_online)
        
        self.radio_fetch_recent = QRadioButton("最近活跃")
        self.radio_fetch_recent.setChecked(True)
        self.radio_fetch_recent.setToolTip("采集最近7天活跃的成员")
        fetch_layout.addWidget(self.radio_fetch_recent)
        
        # 采集成员按钮
        self.btn_fetch_members = QPushButton("⬇️ 采集成员")
        self.btn_fetch_members.setMinimumWidth(120)
        self.btn_fetch_members.clicked.connect(self._on_fetch_members)
        self.btn_fetch_members.setToolTip("从群组中采集成员并自动添加到发送对象列表")
        try:
            self.btn_fetch_members.setIcon(self.style().standardIcon(self.style().SP_ArrowDown))
        except Exception:
            pass
        fetch_layout.addWidget(self.btn_fetch_members)
        
        fetch_layout.addStretch(1)
        grid.addWidget(grp_fetch, 0, 0, 1, 1)

        # 群发（右列）
        grp_send = QGroupBox("📤 批量发送")
        send_layout = QVBoxLayout(grp_send)

        # 消息输入区域
        msg_label = QLabel("📝 消息内容：")
        send_layout.addWidget(msg_label)
        
        # 多行文本输入框
        self.input_message = QTextEdit()
        self.input_message.setPlainText("这是测试消息")
        self.input_message.setPlaceholderText("请输入要发送的消息内容，支持多行文本...")
        self.input_message.setMinimumHeight(90)
        self.input_message.setMaximumHeight(150)
        self.input_message.setObjectName("msgInput")  # 用于QSS样式
        # 设置内联样式确保边框显示
        self.input_message.setStyleSheet("""
            QTextEdit#msgInput {
                border: 2px solid #b0b0b0;
                border-radius: 6px;
                padding: 10px;
                background: #ffffff;
                font-size: 14px;
                font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
                line-height: 1.5;
            }
            QTextEdit#msgInput:focus {
                border: 2px solid #1976d2;
                background: #f8fbff;
            }
            QTextEdit#msgInput:hover {
                border: 2px solid #909090;
            }
        """)
        send_layout.addWidget(self.input_message)
        
        # 图片选择按钮行
        img_row = QHBoxLayout()
        self.btn_pick_image = QPushButton("🖼️ 选择图片")
        self.btn_pick_image.clicked.connect(self._on_pick_image)
        self.btn_pick_image.setToolTip("选择要随消息一起发送的图片（可选）")
        self.btn_pick_image.setMinimumWidth(100)
        try:
            self.btn_pick_image.setIcon(self.style().standardIcon(self.style().SP_DirIcon))
        except Exception:
            pass
        img_row.addWidget(self.btn_pick_image)
        
        # 清空图片按钮
        self.btn_clear_image = QPushButton("🗑️ 清空")
        self.btn_clear_image.clicked.connect(self._on_clear_image)
        self.btn_clear_image.setToolTip("清空已选择的图片")
        self.btn_clear_image.setMinimumWidth(80)
        self.btn_clear_image.setVisible(False)  # 默认隐藏
        try:
            self.btn_clear_image.setIcon(self.style().standardIcon(self.style().SP_TrashIcon))
        except Exception:
            pass
        img_row.addWidget(self.btn_clear_image)
        
        self.lbl_image = QLabel("")
        self.lbl_image.setStyleSheet("color: #666; font-size: 12px; padding: 2px 8px;")
        img_row.addWidget(self.lbl_image)
        img_row.addStretch(1)
        send_layout.addLayout(img_row)

        # 设置摘要 + 按钮（改为弹窗配置）
        cfg_row = QHBoxLayout()
        self.lbl_settings = QLabel("")
        cfg_row.addWidget(self.lbl_settings, 1)
        self.btn_settings = QPushButton("⚙️ 发送配置")
        self.btn_settings.clicked.connect(self._on_settings)
        cfg_row.addWidget(self.btn_settings)
        send_layout.addLayout(cfg_row)

        # Progress bar and stats
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        send_layout.addWidget(self.progress_bar)
        
        self.lbl_stats = QLabel("")
        send_layout.addWidget(self.lbl_stats)

        btn_row = QHBoxLayout()
        self.btn_start_send = QPushButton("▶️ 开始发送")
        self.btn_start_send.setMinimumWidth(120)
        self.btn_start_send.clicked.connect(self._on_start_send)
        self.btn_start_send.setToolTip("按照配置参数执行批量发送任务")
        self.btn_stop_send = QPushButton("⏹️ 停止")
        self.btn_stop_send.setMinimumWidth(80)
        self.btn_stop_send.clicked.connect(self._on_stop_send)
        self.btn_stop_send.setEnabled(False)
        self.btn_stop_send.setToolTip("立即停止当前发送任务")
        # 设置按钮放开始群发左侧
        self.btn_settings.setToolTip("配置发送间隔、并发账号数与发送限额")
        btn_row.addWidget(self.btn_settings)
        try:
            self.btn_settings.setIcon(self.style().standardIcon(self.style().SP_FileDialogDetailedView))
            self.btn_start_send.setObjectName("primaryBtn")
            self.btn_start_send.setIcon(self.style().standardIcon(self.style().SP_MediaPlay))
            self.btn_stop_send.setIcon(self.style().standardIcon(self.style().SP_BrowserStop))
        except Exception:
            pass
        btn_row.addWidget(self.btn_start_send)
        btn_row.addWidget(self.btn_stop_send)
        btn_row.addStretch(1)
        send_layout.addLayout(btn_row)

        grid.addWidget(grp_send, 0, 1, 1, 1)

        # 日志区域（带标题和边框）
        log_group = QGroupBox("📋 运行日志")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(8, 8, 8, 8)
        
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("logArea")
        self.log.setStyleSheet("""
            QTextEdit#logArea {
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                padding: 8px;
                background: #fafafa;
                font-family: "Consolas", "Microsoft YaHei", monospace;
                font-size: 13px;
                color: #333;
            }
        """)
        log_layout.addWidget(self.log)
        
        # 使用分割器，上部是控制区，下部是日志
        splitter = QSplitter()
        splitter.setOrientation(Qt.Vertical)
        splitter.addWidget(controls_container)
        splitter.addWidget(log_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter)

    def _setup_accounts(self):
        layout = QVBoxLayout(self.tab_accounts)
        
        # Toolbar
        toolbar = QHBoxLayout()
        self.btn_delete_invalid_accounts = QPushButton("🗑️ 清理异常账号")
        self.btn_delete_invalid_accounts.clicked.connect(self._delete_invalid_accounts)
        self.btn_delete_invalid_accounts.setToolTip("自动清理状态异常的账号（包括封禁、未授权等，不包括未知状态）")
        try:
            self.btn_delete_invalid_accounts.setIcon(self.style().standardIcon(self.style().SP_TrashIcon))
        except Exception:
            pass
        toolbar.addWidget(self.btn_delete_invalid_accounts)
        
        self.btn_refresh_accounts = QPushButton("🔄 刷新状态")
        self.btn_refresh_accounts.clicked.connect(self._refresh_account_status)
        self.btn_refresh_accounts.setToolTip("重新登录所有账号并更新状态")
        try:
            self.btn_refresh_accounts.setIcon(self.style().standardIcon(self.style().SP_BrowserReload))
        except Exception:
            pass
        toolbar.addWidget(self.btn_refresh_accounts)
        
        self.btn_reset_daily_count = QPushButton("🔁 重置今日发送&限制")
        self.btn_reset_daily_count.clicked.connect(self._reset_daily_sent_count)
        self.btn_reset_daily_count.setToolTip("重置所有账号的今日发送计数和限制状态")
        toolbar.addWidget(self.btn_reset_daily_count)
        
        self.btn_update_accounts = QPushButton("📥 更新账号")
        self.btn_update_accounts.clicked.connect(self._update_accounts)
        self.btn_update_accounts.setToolTip("扫描协议号文件夹，自动添加新的账号文件到数据库")
        try:
            self.btn_update_accounts.setIcon(self.style().standardIcon(self.style().SP_FileDialogNewFolder))
        except Exception:
            pass
        toolbar.addWidget(self.btn_update_accounts)
        
        self.btn_delete_accounts = QPushButton("❌ 删除选中")
        self.btn_delete_accounts.clicked.connect(self._delete_selected_accounts)
        self.btn_delete_accounts.setToolTip("删除选中的账号（会同时删除相关的发送记录）")
        try:
            self.btn_delete_accounts.setIcon(self.style().standardIcon(self.style().SP_TrashIcon))
        except Exception:
            pass
        toolbar.addWidget(self.btn_delete_accounts)
        
        self.btn_debug_accounts = QPushButton("🔍 调试账号")
        self.btn_debug_accounts.clicked.connect(self._debug_accounts)
        self.btn_debug_accounts.setToolTip("调试账号文件读取和路径问题")
        try:
            self.btn_debug_accounts.setIcon(self.style().standardIcon(self.style().SP_FileDialogDetailedView))
        except Exception:
            pass
        toolbar.addWidget(self.btn_debug_accounts)
        
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        
        self.table_accounts = QTableWidget(0, 7)
        self.table_accounts.setHorizontalHeaderLabels(["ID", "手机号", "会话文件", "账号状态", "发送状态", "今日已发", "最近登录"])
        self._beautify_table(self.table_accounts)
        layout.addWidget(self.table_accounts)
        
        # Pagination
        page_bar = QHBoxLayout()
        self.btn_prev_accounts = QPushButton("上一页")
        self.btn_next_accounts = QPushButton("下一页")
        self.lbl_page_accounts = QLabel("第 1 页")
        self.btn_prev_accounts.clicked.connect(lambda: self._change_page("accounts", -1))
        self.btn_next_accounts.clicked.connect(lambda: self._change_page("accounts", 1))
        page_bar.addWidget(self.btn_prev_accounts)
        page_bar.addWidget(self.lbl_page_accounts)
        page_bar.addWidget(self.btn_next_accounts)
        page_bar.addStretch(1)
        layout.addLayout(page_bar)
        
        self._accounts_page = 0
        self._accounts_per_page = 20

    def _setup_targets(self):
        layout = QVBoxLayout(self.tab_targets)
        
        # Toolbar
        toolbar = QHBoxLayout()
        self.btn_add_target = QPushButton("➕ 添加对象")
        self.btn_delete_target = QPushButton("❌ 删除")
        self.btn_reset_status = QPushButton("🔄 重置状态")
        self.btn_clear_sent_targets = QPushButton("✅ 清空已发")
        self.btn_clear_all_targets = QPushButton("🗑️ 全部清空")
        self.btn_add_target.clicked.connect(self._add_targets)
        self.btn_add_target.setToolTip("批量添加发送对象（支持用户名/手机号，一行一个）")
        self.btn_delete_target.clicked.connect(self._delete_target)
        self.btn_reset_status.clicked.connect(self._reset_target_status)
        self.btn_reset_status.setToolTip("将所有非待发送状态的目标（已发送、发送失败等）重置为待发送状态")
        self.btn_clear_sent_targets.clicked.connect(self._clear_sent_targets)
        self.btn_clear_sent_targets.setToolTip("清空所有已成功发送的对象")
        self.btn_clear_all_targets.clicked.connect(self._clear_all_targets)
        self.btn_clear_all_targets.setToolTip("清空所有发送对象（包含所有状态）")
        try:
            self.btn_add_target.setIcon(self.style().standardIcon(self.style().SP_FileDialogNewFolder))
            self.btn_clear_all_targets.setIcon(self.style().standardIcon(self.style().SP_TrashIcon))
        except Exception:
            pass
        toolbar.addWidget(self.btn_add_target)
        toolbar.addWidget(self.btn_delete_target)
        toolbar.addWidget(self.btn_reset_status)
        toolbar.addWidget(self.btn_clear_sent_targets)
        toolbar.addWidget(self.btn_clear_all_targets)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        
        self.table_targets = QTableWidget(0, 5)
        self.table_targets.setHorizontalHeaderLabels(["ID", "接收对象", "来源渠道", "发送状态", "失败原因"])
        self._beautify_table(self.table_targets)
        layout.addWidget(self.table_targets)
        
        # Pagination
        page_bar = QHBoxLayout()
        self.btn_prev_targets = QPushButton("上一页")
        self.btn_next_targets = QPushButton("下一页")
        self.lbl_page_targets = QLabel("第 1 页")
        self.btn_prev_targets.clicked.connect(lambda: self._change_page("targets", -1))
        self.btn_next_targets.clicked.connect(lambda: self._change_page("targets", 1))
        page_bar.addWidget(self.btn_prev_targets)
        page_bar.addWidget(self.lbl_page_targets)
        page_bar.addWidget(self.btn_next_targets)
        page_bar.addStretch(1)
        layout.addLayout(page_bar)
        
        self._targets_page = 0
        self._targets_per_page = 50

    def _setup_groups(self):
        layout = QVBoxLayout(self.tab_groups)
        
        # Toolbar
        toolbar = QHBoxLayout()
        self.btn_add_group = QPushButton("➕ 添加群组")
        self.btn_delete_group = QPushButton("❌ 删除")
        self.btn_clear_fetched_groups = QPushButton("✅ 清空已采")
        self.btn_clear_all_groups = QPushButton("🗑️ 全部清空")
        self.btn_reset_groups = QPushButton("🔄 重置状态")
        self.btn_add_group.clicked.connect(self._add_groups)
        self.btn_add_group.setToolTip("批量添加群组（支持链接/用户名，一行一个）")
        self.btn_delete_group.clicked.connect(self._delete_group)
        self.btn_clear_fetched_groups.clicked.connect(self._clear_fetched_groups)
        self.btn_clear_fetched_groups.setToolTip("清空所有已完成成员采集的群组")
        self.btn_clear_all_groups.clicked.connect(self._clear_all_groups)
        self.btn_clear_all_groups.setToolTip("清空所有群组（包含所有状态）")
        self.btn_reset_groups.clicked.connect(self._reset_groups)
        try:
            self.btn_add_group.setIcon(self.style().standardIcon(self.style().SP_FileDialogNewFolder))
            self.btn_clear_all_groups.setIcon(self.style().standardIcon(self.style().SP_TrashIcon))
        except Exception:
            pass
        toolbar.addWidget(self.btn_add_group)
        toolbar.addWidget(self.btn_delete_group)
        toolbar.addWidget(self.btn_clear_fetched_groups)
        toolbar.addWidget(self.btn_clear_all_groups)
        toolbar.addWidget(self.btn_reset_groups)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        
        self.table_groups = QTableWidget(0, 5)
        self.table_groups.setHorizontalHeaderLabels(["ID", "群组链接/用户名", "已加入", "已采集", "最近采集时间"])
        self._beautify_table(self.table_groups)
        layout.addWidget(self.table_groups)
        
        # Pagination
        page_bar = QHBoxLayout()
        self.btn_prev_groups = QPushButton("上一页")
        self.btn_next_groups = QPushButton("下一页")
        self.lbl_page_groups = QLabel("第 1 页")
        self.btn_prev_groups.clicked.connect(lambda: self._change_page("groups", -1))
        self.btn_next_groups.clicked.connect(lambda: self._change_page("groups", 1))
        page_bar.addWidget(self.btn_prev_groups)
        page_bar.addWidget(self.lbl_page_groups)
        page_bar.addWidget(self.btn_next_groups)
        page_bar.addStretch(1)
        layout.addLayout(page_bar)
        
        self._groups_page = 0
        self._groups_per_page = 20

    def refresh(self):
        with self.repo.session() as s:
            from ..db.models import Account, Target, Group
            
            # Accounts with pagination
            total_accs = s.query(Account).count()
            offset = self._accounts_page * self._accounts_per_page
            accs = s.query(Account).offset(offset).limit(self._accounts_per_page).all()
            
            # fill combo (全部账号)
            all_accs = s.query(Account).all()
            self.combo_account.clear()
            for a in all_accs:
                self.combo_account.addItem(f"{a.phone}", a.id)
                
            self.table_accounts.setRowCount(len(accs))
            for r, a in enumerate(accs):
                self.table_accounts.setItem(r, 0, QTableWidgetItem(str(a.id)))
                self.table_accounts.setItem(r, 1, QTableWidgetItem(a.phone or ""))
                self.table_accounts.setItem(r, 2, QTableWidgetItem(a.session_file or ""))
                
                # 格式化账号状态
                if a.is_limited and a.limited_until:
                    # 账号被限制，显示限制状态
                    from datetime import datetime
                    now = datetime.utcnow()
                    if now < a.limited_until:
                        remaining = a.limited_until - now
                        hours = int(remaining.total_seconds() // 3600)
                        minutes = int((remaining.total_seconds() % 3600) // 60)
                        status_text = f"⏰ 限制 ({hours}h{minutes}m)"
                        status_color = "#ffc107"  # 黄色
                    else:
                        # 限制期已过，但状态还没更新
                        status_text = "⏰ 限制 (已过期)"
                        status_color = "#ffc107"
                else:
                    # 正常状态
                    status_text, status_color = self._format_account_status(a.status or "")
                
                status_item = QTableWidgetItem(status_text)
                status_item.setForeground(QBrush(QColor(status_color)))
                font = QFont()
                font.setBold(True)
                status_item.setFont(font)
                self.table_accounts.setItem(r, 3, status_item)
                
                # 发送状态（新增列）
                send_status = getattr(a, 'send_status', '未启用') or '未启用'
                send_status_item = QTableWidgetItem(send_status)
                
                # 根据发送状态设置颜色
                if send_status == "正在发送":
                    send_status_item.setForeground(QBrush(QColor("#27ae60")))  # 绿色：正在发送
                elif send_status == "等待发送":
                    send_status_item.setForeground(QBrush(QColor("#f39c12")))  # 橙色：等待发送
                else:
                    send_status_item.setForeground(QBrush(QColor("#95a5a6")))  # 灰色：未启用
                
                font = QFont()
                font.setBold(True)
                send_status_item.setFont(font)
                self.table_accounts.setItem(r, 4, send_status_item)
                
                # 今日发送数量（带颜色提示）
                from datetime import datetime
                import pytz
                shanghai_tz = pytz.timezone('Asia/Shanghai')
                today = datetime.now(shanghai_tz).strftime("%Y-%m-%d")
                today_count = a.daily_sent_count or 0
                
                # 如果不是今天的数据，显示为0
                if a.last_sent_date != today:
                    today_count = 0
                
                daily_limit = self._settings.get('daily_limit', 0)
                daily_item = QTableWidgetItem(str(today_count))
                
                # 根据发送量设置颜色
                if daily_limit > 0:
                    if today_count >= daily_limit:
                        daily_item.setForeground(QBrush(QColor("#e74c3c")))  # 红色：已达上限
                        font_limit = QFont()
                        font_limit.setBold(True)
                        daily_item.setFont(font_limit)
                    elif today_count >= daily_limit * 0.8:
                        daily_item.setForeground(QBrush(QColor("#f39c12")))  # 橙色：接近上限
                    else:
                        daily_item.setForeground(QBrush(QColor("#27ae60")))  # 绿色：正常
                 
                self.table_accounts.setItem(r, 5, daily_item)
                self.table_accounts.setItem(r, 6, QTableWidgetItem(str(a.last_login_at or "")))
        
            total_pages_acc = (total_accs + self._accounts_per_page - 1) // self._accounts_per_page
            self.lbl_page_accounts.setText(f"第 {self._accounts_page + 1}/{total_pages_acc} 页")
            self.btn_prev_accounts.setEnabled(self._accounts_page > 0)
            self.btn_next_accounts.setEnabled(self._accounts_page < total_pages_acc - 1)

            # Targets with pagination
            total_tgs = s.query(Target).count()
            offset_tgs = self._targets_page * self._targets_per_page
            tgs = s.query(Target).offset(offset_tgs).limit(self._targets_per_page).all()
            
            self.table_targets.setRowCount(len(tgs))
            for r, t in enumerate(tgs):
                self.table_targets.setItem(r, 0, QTableWidgetItem(str(t.id)))
                self.table_targets.setItem(r, 1, QTableWidgetItem(t.identifier))
                self.table_targets.setItem(r, 2, QTableWidgetItem(t.source or ""))
                
                # 格式化发送状态
                status_text, status_color = self._format_send_status(t.status or "")
                status_item = QTableWidgetItem(status_text)
                status_item.setForeground(QBrush(QColor(status_color)))
                font = QFont()
                font.setBold(True)
                status_item.setFont(font)
                self.table_targets.setItem(r, 3, status_item)
                
                # 失败原因
                fail_reason = ""
                if t.status == "failed" and t.fail_reason:
                    fail_reason = t.fail_reason
                fail_item = QTableWidgetItem(fail_reason)
                if fail_reason:
                    fail_item.setForeground(QBrush(QColor("#dc3545")))  # 红色
                self.table_targets.setItem(r, 4, fail_item)
            
            total_pages_tgs = (total_tgs + self._targets_per_page - 1) // self._targets_per_page
            self.lbl_page_targets.setText(f"第 {self._targets_page + 1}/{total_pages_tgs} 页")
            self.btn_prev_targets.setEnabled(self._targets_page > 0)
            self.btn_next_targets.setEnabled(self._targets_page < total_pages_tgs - 1)

            # Groups with pagination
            total_gps = s.query(Group).count()
            offset_gps = self._groups_page * self._groups_per_page
            gps = s.query(Group).offset(offset_gps).limit(self._groups_per_page).all()
            
            self.table_groups.setRowCount(len(gps))
            for r, g in enumerate(gps):
                self.table_groups.setItem(r, 0, QTableWidgetItem(str(g.id)))
                self.table_groups.setItem(r, 1, QTableWidgetItem(g.link_or_username))
                self.table_groups.setItem(r, 2, QTableWidgetItem("是" if g.joined else "否"))
                self.table_groups.setItem(r, 3, QTableWidgetItem("是" if g.fetched else "否"))
                self.table_groups.setItem(r, 4, QTableWidgetItem(str(g.last_fetched_at or "")))
            
            total_pages_gps = (total_gps + self._groups_per_page - 1) // self._groups_per_page
            self.lbl_page_groups.setText(f"第 {self._groups_page + 1}/{total_pages_gps} 页")
            self.btn_prev_groups.setEnabled(self._groups_page > 0)
            self.btn_next_groups.setEnabled(self._groups_page < total_pages_gps - 1)

    def _setup_help(self):
        """设置使用说明页面"""
        from PySide6.QtWidgets import QScrollArea
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont
        
        layout = QVBoxLayout(self.tab_help)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 创建滚动区域
        scroll_area = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        
        # 标题
        title_label = QLabel("📖 Telegram 群发助手 - 使用说明")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #2E86AB; margin-bottom: 20px;")
        scroll_layout.addWidget(title_label)
        
        # 快速开始
        self._add_help_section(scroll_layout, "🚀 快速开始", [
            "1️⃣ 设置项目根目录 → 2️⃣ 初始化数据 → 3️⃣ 准备账号文件 → 4️⃣ 添加群组 → 5️⃣ 采集成员 → 6️⃣ 配置发送 → 7️⃣ 开始群发"
        ])
        
        # 步骤1：设置项目根目录
        self._add_help_section(scroll_layout, "1️⃣ 设置项目根目录", [
            "📁 首次使用需要手动设置项目根目录：",
            "   • 在顶部输入框中输入你的项目根目录路径（.exe文件所在目录）",
            "   • 例如：D:\\python\\telegram群发",
            "   • 点击 '💾 保存' 按钮保存设置",
            "   • 项目根目录应该包含以下文件夹：",
            "     - 协议号/ - 存放账号文件",
            "     - 群发目标/ - 存放目标用户文件", 
            "     - 群/ - 存放群组文件",
            "     - data/ - 存放数据库文件"
        ])
        
        # 步骤2：初始化数据
        self._add_help_section(scroll_layout, "2️⃣ 初始化数据", [
            "🔧 设置项目根目录后，需要初始化数据：",
            "   • 点击 '🔧 初始化数据' 按钮",
            "   • 系统会创建数据库表结构和目录",
            "   • 扫描协议号文件夹中的账号文件",
            "   • 自动检测账号状态",
            "   • 初始化完成后按钮会变为 '🔄 重新初始化'",
            "",
            "💡 重新初始化：",
            "   • 如果已初始化过，按钮会变为橙色 '🔄 重新初始化'",
            "   • 点击可以重新扫描账号和检测状态",
            "   • 会保留现有的发送对象和群组数据"
        ])
        
        # 步骤3：准备账号文件
        self._add_help_section(scroll_layout, "3️⃣ 准备账号文件", [
            "📁 在项目根目录的 '协议号' 文件夹中放入账号文件：",
            "   • 将 .json 和 .session 文件放入 协议号/ 文件夹",
            "   • 文件名格式：手机号.json 和 手机号.session",
            "   • 例如：916201652131.json 和 916201652131.session",
            "   • 支持多个账号，系统会自动识别并加载",
            "   • 添加新账号后，点击 '🔄 重新初始化' 更新账号列表"
        ])
        
        # 步骤4：添加群组
        self._add_help_section(scroll_layout, "4️⃣ 添加群组", [
            "👥 在 '群组管理' 页面添加要采集的群组：",
            "   • 点击 '+ 添加群组' 按钮",
            "   • 输入群组链接或用户名（每行一个）",
            "   • 支持格式：https://t.me/groupname 或 @groupname 或 groupname",
            "   • 系统会自动去重，避免重复添加"
        ])
        
        # 步骤5：采集成员
        self._add_help_section(scroll_layout, "5️⃣ 采集群成员", [
            "⬇️ 在 '工作台' 页面采集群成员：",
            "   • 选择要使用的账号（下拉框）",
            "   • 选择采集模式：",
            "     - 最近活跃：采集最近7天活跃的成员（推荐）",
            "     - 在线成员：只采集当前在线的成员",
            "     - 全部成员：采集所有群成员",
            "   • 点击 '⬇️ 采集成员' 按钮",
            "   • 系统会自动加入群组并采集成员信息",
            "   • 采集的成员会自动添加到 '发送对象' 列表",
            "   • 自动过滤机器人和注销账号",
            "",
            "💡 采集模式说明：",
            "   • 最近活跃：采集最近活跃的用户，平衡质量和数量（推荐）",
            "   • 在线成员：只采集当前在线的用户，质量最高",
            "   • 全部成员：采集所有成员，数量最多但可能包含僵尸用户"
        ])
        
        # 步骤6：配置发送
        self._add_help_section(scroll_layout, "6️⃣ 配置发送内容", [
            "📝 在 '工作台' 页面配置发送内容：",
            "   • 输入要发送的消息内容（支持多行文本）",
            "   • 可选：点击 '🖼️ 选择图片' 添加图片",
            "   • 点击 '⚙️ 发送配置' 设置发送参数",
            "   • 配置完成后点击 '▶️ 开始发送'"
        ])
        
        # 步骤7：开始发送
        self._add_help_section(scroll_layout, "7️⃣ 开始发送", [
            "📤 配置完成后开始群发：",
            "   • 点击 '▶️ 开始发送' 按钮",
            "   • 系统会显示发送进度和统计信息",
            "   • 实时查看运行日志了解发送详情",
            "   • 可以随时点击 '■ 停止' 按钮停止发送",
            "   • 发送完成后可以查看成功和失败统计",
            "",
            "💡 发送策略：",
            "   • 系统会智能分配目标给不同账号",
            "   • 当账号遇到限制时会自动切换到其他账号",
            "   • 确保所有账号都被充分利用",
            "   • 支持动态任务调度，提高发送效率"
        ])
        
        # 发送配置说明
        self._add_help_section(scroll_layout, "⚙️ 发送配置详解", [
            "📊 发送间隔模式：",
            "   • 随机间隔：在最小值和最大值之间随机选择",
            "   • 固定间隔：使用固定的发送间隔时间",
            "",
            "🔢 单账号发送上限：",
            "   • 每个账号在一次任务中最多发送的消息数量",
            "   • 请根据实际情况设置，建议先少量测试",
            "",
            "⚡ 并发账号数量：",
            "   • 同时使用的账号数量",
            "   • 请根据账号质量和网络环境调整",
            "",
            "📅 每日发送上限：",
            "   • 每个账号每天最多发送的消息数量",
            "   • 0 表示无限制，请谨慎设置避免风控"
        ])
        
        # 功能说明
        self._add_help_section(scroll_layout, "📋 各页面功能说明", [
            "📊 工作台：",
            "   • 选择账号、采集成员、配置发送内容",
            "   • 实时显示发送进度和统计信息",
            "   • 查看详细的运行日志",
            "",
            "👤 账号管理：",
            "   • 查看所有账号的状态和统计信息",
            "   • 清理异常账号、刷新状态、重置计数",
            "   • 监控每日发送数量和登录状态",
            "",
            "🎯 发送对象：",
            "   • 管理所有待发送的目标用户",
            "   • 查看发送状态和失败原因",
            "   • 重置状态、清空已发送目标",
            "",
            "👥 群组管理：",
            "   • 管理要采集的群组列表",
            "   • 查看群组加入和采集状态",
            "   • 清空已采集的群组"
        ])
        
        # 注意事项
        self._add_help_section(scroll_layout, "⚠️ 重要注意事项", [
            "🛡️ 风控建议：",
            "   • 首次使用请务必先少量测试，观察账号状态",
            "   • 根据测试结果逐步调整发送参数",
            "   • 避免发送敏感或违规内容",
            "   • 定期检查账号状态，及时发现问题",
            "",
            "📞 电话号码发送：",
            "   • 发送给电话号码时会自动添加为联系人",
            "   • 系统会自动标准化电话号码格式（添加+号）",
            "   • 如果添加联系人失败，可能需要手动添加",
            "   • 某些用户可能设置了隐私限制，无法接收陌生人消息",
            "",
            "💡 使用技巧：",
            "   • 建议先用 1-3 个目标测试发送功能",
            "   • 测试成功后，再逐步增加发送数量",
            "   • 使用 '重置状态' 功能重新发送失败的目标",
            "   • 关注运行日志，了解发送详情和账号状态",
            "",
            "🔧 故障排除：",
            "   • 如果发送失败，检查账号是否正常",
            "   • 如果采集失败，确认群组链接是否正确",
            "   • 如果界面卡顿，尝试重启应用",
            "   • 电话号码发送失败时，检查号码格式和用户隐私设置"
        ])
        
        # 部署说明
        self._add_help_section(scroll_layout, "📦 程序部署", [
            "🚀 程序支持灵活部署：",
            "   • 可以将程序文件夹复制到任何位置运行",
            "   • 需要手动设置项目根目录",
            "   • 首次使用需要手动初始化数据",
            "   • 支持打包成单个可执行文件分发",
            "",
            "📁 目录结构：",
            "   • 协议号/ - 存放账号文件",
            "   • 群发目标/ - 存放目标用户文件",
            "   • 群/ - 存放群组文件",
            "   • data/ - 存放数据库和运行数据",
            "   • assets/ - 存放样式和资源文件",
            "",
            "🔧 手动操作：",
            "   • 需要手动设置项目根目录",
            "   • 需要手动初始化数据库和目录",
            "   • 添加新账号后需要重新初始化",
            "   • 所有操作都由用户完全控制"
        ])
        
        # 数据存储
        self._add_help_section(scroll_layout, "💾 数据存储", [
            "📁 所有数据存储在 SQLite 数据库中：",
            "   • 数据库文件：data/app.db",
            "   • 账号信息、发送对象、群组信息都会自动保存",
            "   • 发送记录和日志也会持久化存储",
            "   • 支持数据备份和恢复"
        ])
        
        # 版本信息
        version_label = QLabel("📱 Telegram 群发助手 v2.0 - Qt版本")
        version_font = QFont()
        version_font.setItalic(True)
        version_label.setFont(version_font)
        version_label.setAlignment(Qt.AlignCenter)
        version_label.setStyleSheet("color: gray; margin-top: 20px;")
        scroll_layout.addWidget(version_label)
        
        tip_label = QLabel("💡 如有问题，请查看运行日志或重启应用")
        tip_label.setAlignment(Qt.AlignCenter)
        tip_label.setStyleSheet("color: blue; margin-top: 5px;")
        scroll_layout.addWidget(tip_label)
        
        # 设置滚动区域
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        layout.addWidget(scroll_area)
    
    def _add_help_section(self, layout, title, content_list):
        """添加帮助段落"""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont
        
        # 标题
        title_label = QLabel(title)
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #2E86AB; margin-top: 15px; margin-bottom: 5px;")
        layout.addWidget(title_label)
        
        # 内容
        for content in content_list:
            if content.strip():  # 跳过空行
                content_label = QLabel(content)
                content_label.setWordWrap(True)
                content_label.setStyleSheet("margin-left: 20px; margin-bottom: 2px;")
                layout.addWidget(content_label)
            else:
                # 空行
                spacer = QLabel("")
                spacer.setFixedHeight(5)
                layout.addWidget(spacer)

    def _beautify_table(self, table: QTableWidget) -> None:
        header = table.horizontalHeader()
        # Stretch columns to fill
        try:
            from PySide6.QtWidgets import QHeaderView
            header.setSectionResizeMode(QHeaderView.Stretch)
        except Exception:
            pass
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        # 启用双击编辑和复制功能
        table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.SelectedClicked)
        # 启用键盘选择
        table.setSelectionMode(QTableWidget.ExtendedSelection)
        # 设置行高
        table.verticalHeader().setDefaultSectionSize(45)

    def _format_account_status(self, status: str) -> tuple[str, str]:
        """格式化账号状态，返回(显示文本, 颜色)"""
        status_map = {
            "ok": ("✅ 正常", "#28a745"),
            "active": ("✅ 活跃", "#28a745"),
            "error": ("❌ 错误", "#dc3545"),
            "banned": ("🚫 已封禁", "#dc3545"),
            "frozen": ("🧊 冻结", "#9b59b6"),  # Purple
            "unauthorized": ("⚠️ 未授权", "#ffc107"),
            "unknown": ("❓ 未检测", "#ff9800"),  # 橙色，表示需要检测
            "login_failed": ("⛔ 登录失败", "#dc3545"),
            "limited": ("⏰ 已限制", "#ff6b35"),
        }
        return status_map.get(status.lower() if status else "", (f"❓ {status}", "#6c757d"))

    def _update_targets_table_only(self):
        """只更新目标表格，用于实时刷新发送状态"""
        try:
            with self.repo.session() as s:
                from ..db.models import Target
                
                # 获取当前页的目标数据
                total_tgs = s.query(Target).count()
                offset_tgs = self._targets_page * self._targets_per_page
                tgs = s.query(Target).offset(offset_tgs).limit(self._targets_per_page).all()
                
                # 更新表格数据
                self.table_targets.setRowCount(len(tgs))
                for r, t in enumerate(tgs):
                    self.table_targets.setItem(r, 0, QTableWidgetItem(str(t.id)))
                    self.table_targets.setItem(r, 1, QTableWidgetItem(t.identifier))
                    self.table_targets.setItem(r, 2, QTableWidgetItem(t.source or ""))
                    
                    # 格式化发送状态
                    status_text, status_color = self._format_send_status(t.status or "")
                    status_item = QTableWidgetItem(status_text)
                    status_item.setForeground(QBrush(QColor(status_color)))
                    font = QFont()
                    font.setBold(True)
                    status_item.setFont(font)
                    self.table_targets.setItem(r, 3, status_item)
                    
                    # 失败原因
                    fail_reason = ""
                    if t.status == "failed" and t.fail_reason:
                        fail_reason = t.fail_reason
                    fail_item = QTableWidgetItem(fail_reason)
                    if fail_reason:
                        fail_item.setForeground(QBrush(QColor("#dc3545")))  # 红色
                    self.table_targets.setItem(r, 4, fail_item)
                
                # 更新分页信息
                total_pages_tgs = (total_tgs + self._targets_per_page - 1) // self._targets_per_page
                self.lbl_page_targets.setText(f"第 {self._targets_page + 1}/{total_pages_tgs} 页")
                self.btn_prev_targets.setEnabled(self._targets_page > 0)
                self.btn_next_targets.setEnabled(self._targets_page < total_pages_tgs - 1)
                
        except Exception as e:
            print(f"更新目标表格失败: {e}")

    def _update_accounts_table_only(self):
        """只更新账号表格，用于实时刷新发送状态"""
        try:
            with self.repo.session() as s:
                from ..db.models import Account
                
                # 获取当前页的账号数据
                total_accs = s.query(Account).count()
                offset_accs = self._accounts_page * self._accounts_per_page
                accs = s.query(Account).offset(offset_accs).limit(self._accounts_per_page).all()
                
                # 更新表格数据
                self.table_accounts.setRowCount(len(accs))
                for r, a in enumerate(accs):
                    self.table_accounts.setItem(r, 0, QTableWidgetItem(str(a.id)))
                    self.table_accounts.setItem(r, 1, QTableWidgetItem(a.phone or ""))
                    self.table_accounts.setItem(r, 2, QTableWidgetItem(a.session_file or ""))
                    
                    # 格式化账号状态
                    if a.is_limited and a.limited_until:
                        # 账号被限制，显示限制状态
                        from datetime import datetime
                        now = datetime.utcnow()
                        if now < a.limited_until:
                            remaining = a.limited_until - now
                            hours = int(remaining.total_seconds() // 3600)
                            minutes = int((remaining.total_seconds() % 3600) // 60)
                            status_text = f"限制 ({hours}h{minutes}m)"
                            status_color = "#f39c12"  # 橙色
                        else:
                            status_text = "正常"
                            status_color = "#27ae60"  # 绿色
                    else:
                        status_text = a.status or "未知"
                        if status_text == "ok":
                            status_text = "正常"
                            status_color = "#27ae60"  # 绿色
                        elif status_text == "limited":
                            status_text = "限制"
                            status_color = "#f39c12"  # 橙色
                        elif status_text == "banned":
                            status_text = "封禁"
                            status_color = "#e74c3c"  # 红色
                        else:
                            status_color = "#95a5a6"  # 灰色
                    
                    status_item = QTableWidgetItem(status_text)
                    status_item.setForeground(QBrush(QColor(status_color)))
                    font = QFont()
                    font.setBold(True)
                    status_item.setFont(font)
                    self.table_accounts.setItem(r, 3, status_item)
                    
                    # 发送状态（新增列）
                    send_status = getattr(a, 'send_status', '未启用') or '未启用'
                    send_status_item = QTableWidgetItem(send_status)
                    
                    # 根据发送状态设置颜色
                    if send_status == "正在发送":
                        send_status_item.setForeground(QBrush(QColor("#27ae60")))  # 绿色：正在发送
                    elif send_status == "等待发送":
                        send_status_item.setForeground(QBrush(QColor("#f39c12")))  # 橙色：等待发送
                    else:
                        send_status_item.setForeground(QBrush(QColor("#95a5a6")))  # 灰色：未启用
                    
                    font = QFont()
                    font.setBold(True)
                    send_status_item.setFont(font)
                    self.table_accounts.setItem(r, 4, send_status_item)
                    
                    # 今日发送数量（带颜色提示）
                    from datetime import datetime
                    import pytz
                    shanghai_tz = pytz.timezone('Asia/Shanghai')
                    today = datetime.now(shanghai_tz).strftime("%Y-%m-%d")
                    today_count = a.daily_sent_count or 0
                    
                    # 如果不是今天的数据，显示为0
                    if a.last_sent_date != today:
                        today_count = 0
                    
                    daily_limit = self._settings.get('daily_limit', 0)
                    daily_item = QTableWidgetItem(str(today_count))
                    
                    # 根据发送量设置颜色
                    if daily_limit > 0:
                        if today_count >= daily_limit:
                            daily_item.setForeground(QBrush(QColor("#e74c3c")))  # 红色：已达上限
                            font_limit = QFont()
                            font_limit.setBold(True)
                            daily_item.setFont(font_limit)
                        elif today_count >= daily_limit * 0.8:
                            daily_item.setForeground(QBrush(QColor("#f39c12")))  # 橙色：接近上限
                        else:
                            daily_item.setForeground(QBrush(QColor("#27ae60")))  # 绿色：正常
                     
                    self.table_accounts.setItem(r, 5, daily_item)
                    self.table_accounts.setItem(r, 6, QTableWidgetItem(str(a.last_login_at or "")))
                
                # 更新分页信息
                total_pages_acc = (total_accs + self._accounts_per_page - 1) // self._accounts_per_page
                self.lbl_page_accounts.setText(f"第 {self._accounts_page + 1}/{total_pages_acc} 页")
                self.btn_prev_accounts.setEnabled(self._accounts_page > 0)
                self.btn_next_accounts.setEnabled(self._accounts_page < total_pages_acc - 1)
                
        except Exception as e:
            print(f"更新账号表格失败: {e}")

    def _format_send_status(self, status: str) -> tuple[str, str]:
        """格式化发送状态，返回(显示文本, 颜色)"""
        status_map = {
            "pending": ("⏳ 待发送", "#6c757d"),
            "sent": ("✅ 已发送", "#28a745"),
            "failed": ("❌ 发送失败", "#dc3545"),
            "skipped": ("⏭️ 已跳过", "#ffc107"),
        }
        return status_map.get(status.lower() if status else "", (f"❓ {status}", "#6c757d"))

    def _get_selected_account_index(self) -> int:
        idx = self.combo_account.currentIndex()
        return max(idx, 0)

    def _append_log(self, text: str) -> None:
        self.log.append(text)
        # 自动滚动到底部
        from PySide6.QtGui import QTextCursor
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _on_search_groups(self):
        """采集群组"""
        dlg = SearchGroupsDialog(self)
        if dlg.exec():
            keywords = dlg.get_keywords()
            search_limit = dlg.get_search_limit()
            if not keywords:
                self._append_log("❌ 请输入至少一个关键词")
                return
            
            account_index = self._get_selected_account_index()
            self._append_log(f"🔍 开始采集群组，使用账号序号 {account_index}，关键词数: {len(keywords)} 个，每个关键词搜索 {search_limit} 个群组")
            
            def on_progress(keyword: str, stats: dict):
                """每完成一个关键词就打印进度"""
                if 'error' in stats:
                    self._append_log(f"❌ 关键词 '{keyword}': {stats['error']}")
                else:
                    self._append_log(
                        f"✓ 关键词 '{keyword}': 找到 {stats.get('found', 0)} 个群组, "
                        f"新增 {stats.get('added', 0)} 个"
                    )
                    # 显示调试信息
                    if 'debug' in stats and stats['debug']:
                        for group_info in stats['debug']:
                            self._append_log(f"  📋 {group_info}")
            
            def worker():
                try:
                    from ..core.group_searcher import search_groups_by_keywords
                    totals = asyncio.run(
                        search_groups_by_keywords(
                            self.repo,
                            keywords,
                            account_index=account_index,
                            search_limit=search_limit,
                            on_progress=on_progress
                        )
                    )
                    self._append_log(
                        f"🎉 搜索完成: 共搜索 {totals.get('total_keywords', 0)} 个关键词, "
                        f"找到 {totals.get('groups_found', 0)} 个群组, "
                        f"新增 {totals.get('groups_added', 0)} 个"
                    )
                except Exception as e:
                    self._append_log(f"❌ 搜索失败: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    self.refresh()
            
            threading.Thread(target=worker, daemon=True).start()

    def _on_bot_collect_groups(self):
        dlg = BotSearchDialog(self)
        if not dlg.exec():
            return
        keywords = dlg.get_keywords()
        if not keywords:
            self._append_log("❌ 请输入至少一个关键词")
            return
        bot_username = dlg.get_bot_username()
        max_pages = dlg.get_max_pages()
        delay = dlg.get_delay()

        account_index = self._get_selected_account_index()
        self._append_log(f"🤖 机器人采集：{bot_username}，关键词 {len(keywords)} 个，每词最多 {max_pages} 页")

        def on_progress(kw: str, stats: dict):
            if 'error' in stats:
                self._append_log(f"❌ {kw}: {stats['error']}")
            else:
                self._append_log(f"✓ {kw}: 找到 {stats.get('found', 0)}，新增 {stats.get('added', 0)}，页数 {stats.get('pages', 0)}")
                # 显示调试信息
                if 'debug' in stats and stats['debug']:
                    for link in stats['debug']:
                        self._append_log(f"  🔗 {link}")

        def worker():
            try:
                from ..core.bot_group_fetcher import search_groups_via_bot
                totals = asyncio.run(search_groups_via_bot(
                    self.repo,
                    keywords,
                    account_index=account_index,
                    bot_username=bot_username,
                    max_pages_per_keyword=max_pages,
                    per_page_delay_sec=delay,
                    on_progress=on_progress
                ))
                self._append_log(f"🎉 机器人采集完成：找到 {totals.get('groups_found', 0)}，新增 {totals.get('groups_added', 0)}")
            except Exception as e:
                self._append_log(f"❌ 机器人采集失败：{e}")
            finally:
                self.refresh()

        threading.Thread(target=worker, daemon=True).start()

    def _on_fetch_members(self):
        account_index = self._get_selected_account_index()
        
        # 确定采集模式
        if self.radio_fetch_online.isChecked():
            mode = "在线成员"
            self._append_log(f"开始获取在线成员，账号序号 {account_index} ...")
        elif self.radio_fetch_recent.isChecked():
            mode = "最近活跃"
            self._append_log(f"开始获取最近活跃成员，账号序号 {account_index} ...")
        else:
            mode = "全部成员"
            self._append_log(f"开始获取全部成员，账号序号 {account_index} ...")

        def on_group_progress(group_name: str, stats: dict):
            """每完成一个群就打印进度"""
            self._append_log(
                f"✓ {group_name}: 新增成员 {stats.get('members', 0)} 个, "
                f"新增目标 {stats.get('targets', 0)} 个"
            )

        def worker():
            try:
                from ..core.member_fetcher_enhanced import (
                    MemberFilter, 
                    fetch_members_into_db_enhanced,
                    fetch_online_members_only,
                    fetch_recent_members
                )
                
                if self.radio_fetch_online.isChecked():
                    # 只采集在线成员
                    totals = asyncio.run(
                        fetch_online_members_only(
                            self.repo, 
                            account_index=account_index,
                            on_progress=on_group_progress
                        )
                    )
                elif self.radio_fetch_recent.isChecked():
                    # 采集最近7天活跃成员
                    totals = asyncio.run(
                        fetch_recent_members(
                            self.repo, 
                            account_index=account_index,
                            recent_days=7,
                            on_progress=on_group_progress
                        )
                    )
                else:
                    # 采集全部成员（使用原版功能）
                    from ..core.member_fetcher import fetch_members_into_db
                    totals = asyncio.run(
                        fetch_members_into_db(
                            self.repo, 
                            account_index=account_index,
                            on_progress=on_group_progress
                        )
                    )
                
                self._append_log(f"✅ {mode}采集完成: 共处理 {totals.get('groups', 0)} 个群, 新增目标 {totals.get('targets_added', 0)} 个")
                
            except Exception as e:
                self._append_log(f"❌ {mode}采集失败: {e}")
                import traceback
                traceback.print_exc()
            finally:
                self.refresh()

        threading.Thread(target=worker, daemon=True).start()

    def _on_start_send(self):
        message = self.input_message.toPlainText().strip() or "这是测试消息"
        image_path = getattr(self, "_picked_image", None)
        
        # 检查是否有待发送的目标
        with self.repo.session() as s:
            from ..db.models import Target
            pending_count = s.query(Target).filter(Target.status == "pending").count()
            if pending_count == 0:
                self._show_message("没有待发送的目标，请先添加发送对象或重置状态")
                return
        
        self.btn_start_send.setEnabled(False)
        self.btn_stop_send.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self._append_log(f"📤 开始群发任务...")

        # 创建信号对象
        signals = WorkerSignals()
        
        # 连接信号到UI更新方法
        signals.log_message.connect(self._append_log)
        signals.progress_update.connect(self._update_progress)
        signals.cleanup_ui.connect(self._cleanup_ui_after_send)

        def on_progress(stats):
            """进度更新回调"""
            signals.progress_update.emit(stats)

        def on_log(msg):
            """日志更新回调"""
            signals.log_message.emit(msg)

        def worker():
            """工作线程函数 - 使用信号机制更新UI"""
            import time
            
            try:
                on_log("🔧 正在初始化发送引擎...")
                print("DEBUG: 开始初始化发送引擎")
                
                # 验证设置是否有效
                if not self._settings:
                    on_log("❌ 发送设置为空，使用默认设置")
                    self._settings = {"random": True, "min": 15, "max": 15, "fixed": 15, "per": 20, "conc": 6, "daily_limit": 0}
                
                print("DEBUG: 设置验证完成，开始创建SenderEngine")
                self._sender_engine = SenderEngine(self.repo, self._settings, on_progress, on_log)
                print("DEBUG: SenderEngine创建完成")
                on_log("✅ 发送引擎初始化成功")
                on_log("🚀 开始执行发送任务...")
                print("DEBUG: 开始执行发送任务")
                
                # 使用更简单的方式运行异步任务，避免复杂的事件循环管理
                try:
                    print("DEBUG: 准备运行asyncio.run")
                    # 直接运行异步任务，不创建新的事件循环
                    stats = asyncio.run(
                        asyncio.wait_for(
                            self._sender_engine.send_bulk(message, image_path),
                            timeout=1800.0  # 30分钟超时
                        )
                    )
                    print("DEBUG: asyncio.run完成")
                    on_log(f"🎉 群发完成 - 成功: {stats.get('sent', 0)}, 失败: {stats.get('failed', 0)}, 总计: {stats.get('total', 0)}")
                    
                except asyncio.TimeoutError:
                    print("DEBUG: 任务超时")
                    on_log("⏰ 群发任务超时，已自动停止")
                except Exception as send_e:
                    print(f"DEBUG: 发送异常: {send_e}")
                    on_log(f"❌ 发送过程中出错: {send_e}")
                    import traceback
                    on_log(f"发送错误详情: {traceback.format_exc()}")
                    
            except Exception as e:
                print(f"DEBUG: 工作线程异常: {e}")
                import traceback
                error_msg = f"❌ 群发失败: {e}"
                on_log(error_msg)
                on_log(f"错误详情: {traceback.format_exc()}")
                print(f"发送错误详情: {error_msg}\n{traceback.format_exc()}")
            finally:
                try:
                    print("DEBUG: 开始清理")
                    on_log("🔧 正在清理发送引擎...")
                    # 确保发送引擎被停止
                    if self._sender_engine:
                        self._sender_engine.stop()
                        self._sender_engine = None
                        on_log("✅ 发送引擎已停止")
                    
                    # 使用信号触发UI清理
                    print("DEBUG: 发送UI清理信号")
                    signals.cleanup_ui.emit()
                    print("DEBUG: UI清理信号已发送")
                    
                except Exception as cleanup_e:
                    print(f"DEBUG: 清理失败: {cleanup_e}")
                    import traceback
                    traceback.print_exc()

        # 使用守护线程，确保应用关闭时线程也会结束
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _update_progress(self, stats):
        """更新进度条和统计信息，并实时刷新表格数据"""
        try:
            total = stats.get("total", 1)
            sent = stats.get("sent", 0)
            failed = stats.get("failed", 0)
            progress = int((sent + failed) / total * 100) if total > 0 else 0
            self.progress_bar.setValue(progress)
            self.lbl_stats.setText(f"已发送: {sent} | 失败: {failed} | 总计: {total}")
            
            # 实时刷新表格数据，让用户看到发送状态变化
            self._update_targets_table_only()
            # 实时刷新账号状态，让用户看到发送状态变化
            self._update_accounts_table_only()
        except Exception as e:
            print(f"进度更新失败: {e}")

    def _cleanup_ui_after_send(self):
        """在发送完成后清理UI状态"""
        try:
            print("正在恢复按钮状态...")
            self.btn_start_send.setEnabled(True)
            self.btn_stop_send.setEnabled(False)
            print("按钮状态已恢复")
            
            print("正在隐藏进度条...")
            self.progress_bar.setVisible(False)
            self.lbl_stats.setText("")
            print("进度条已隐藏")
            
            print("正在添加结束日志...")
            self._append_log("✅ 发送任务已结束")
            print("结束日志已添加")
            
            print("正在刷新数据...")
            self.refresh()
            print("数据已刷新")
            
        except Exception as ui_e:
            print(f"UI清理失败: {ui_e}")
            import traceback
            traceback.print_exc()

    def _on_stop_send(self):
        if self._sender_engine:
            self._sender_engine.stop()
            self._append_log("⏹️ 正在停止发送任务...")
            # 立即更新UI状态
            self.btn_stop_send.setEnabled(False)
            self.btn_stop_send.setText("⏹️ 停止中...")
            
            # 强制清理UI状态
            from PySide6.QtCore import QTimer
            def force_cleanup():
                try:
                    self._append_log("🛑 强制停止发送任务")
                    self._cleanup_ui_after_send()
                except Exception as e:
                    print(f"强制清理失败: {e}")
            
            # 延迟1秒后强制清理，确保任务被停止
            QTimer.singleShot(1000, force_cleanup)
        else:
            self._append_log("⚠️ 没有正在运行的发送任务")

    def _on_settings(self):
        # 以当前内存设置为初值
        initial = dict(self._settings)
        dlg = SettingsDialog(self, initial)
        if dlg.exec():
            self._settings = dlg.get_settings()
            self.repo.save_setting("send_config", self._settings)
            self._update_settings_summary()

    def _update_settings_summary(self):
        s = self._settings
        if s.get("random", True):
            text = f"随机间隔: {s.get('min', 15)}~{s.get('max', 15)} 秒 | 单号最多发送数: {s.get('per', 20)} | 并发: {s.get('conc', 6)}"
        else:
            text = f"固定间隔: {s.get('fixed', 15)} 秒 | 每号最多: {s.get('per', 20)} | 并发: {s.get('conc', 6)}"
        
        # 添加每日上限信息
        daily_limit = s.get('daily_limit', 0)
        if daily_limit > 0:
            text += f" | 每日上限: {daily_limit}"
        else:
            text += " | 每日上限: 无限制"
        
        self.lbl_settings.setText(text)

    def _on_pick_image(self):
        from PySide6.QtWidgets import QFileDialog
        file, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "Images (*.png *.jpg *.jpeg *.gif)")
        if file:
            self._picked_image = file
            filename = file.split("/")[-1].split("\\")[-1]  # 处理Windows路径
            self.lbl_image.setText(f"✅ 已选：{filename}")
            self.btn_clear_image.setVisible(True)  # 显示清空按钮
        else:
            self._picked_image = None
            self.lbl_image.setText("")
            self.btn_clear_image.setVisible(False)

    def _on_clear_image(self):
        """清空已选择的图片"""
        self._picked_image = None
        self.lbl_image.setText("❌ 已清空图片")
        self.btn_clear_image.setVisible(False)  # 隐藏清空按钮

    def _change_page(self, table: str, direction: int):
        if table == "accounts":
            self._accounts_page = max(0, self._accounts_page + direction)
        elif table == "targets":
            self._targets_page = max(0, self._targets_page + direction)
        elif table == "groups":
            self._groups_page = max(0, self._groups_page + direction)
        self.refresh()

    def _show_message(self, msg: str):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, "提示", msg)

    def _delete_invalid_accounts(self):
        from PySide6.QtWidgets import QMessageBox
        
        # 先统计有多少异常账号（只包含error和unauthorized）
        with self.repo.session() as s:
            from ..db.models import Account
            invalid_accounts = s.query(Account).filter(
                Account.status.in_(["error", "unauthorized"])
            ).all()
            count = len(invalid_accounts)
        
        if count == 0:
            self._show_message("没有异常账号需要删除")
            return
        
        reply = QMessageBox.question(
            self, 
            "确认", 
            f"找到 {count} 个异常账号（状态为error/unauthorized），确定要删除吗？"
        )
        
        if reply == QMessageBox.Yes:
            with self.repo.session() as s:
                from ..db.models import Account
                deleted = s.query(Account).filter(
                    Account.status.in_(["error", "unauthorized"])
                ).delete(synchronize_session=False)
                s.commit()
            
            self._append_log(f"已删除 {deleted} 个异常账号")
            self._accounts_page = 0
            self.refresh()

    def _refresh_account_status(self):
        """刷新所有账号状态 - 同步文件系统并删除不存在的账号"""
        from PySide6.QtWidgets import QMessageBox
        from ..core.syncer import read_accounts_from_files
        import threading
        import asyncio
        
        self._append_log("🔄 开始刷新账号状态...")
        
        try:
            # 1. 读取文件系统中的账号文件
            file_accounts = read_accounts_from_files()
            file_phones = {acc['phone'] for acc in file_accounts}
            
            # 2. 获取数据库中的账号
            with self.repo.session() as s:
                from ..db.models import Account
                db_accounts = s.query(Account).all()
                db_phones = {acc.phone for acc in db_accounts}
            
            # 3. 找出需要删除的账号（数据库中有但文件中没有）
            to_delete = db_phones - file_phones
            to_add = file_phones - db_phones
            
            if to_delete:
                self._append_log(f"🗑️ 发现 {len(to_delete)} 个账号文件已删除，需要从数据库中移除")
                for phone in to_delete:
                    self._append_log(f"   • {phone}")
            
            if to_add:
                self._append_log(f"📥 发现 {len(to_add)} 个新账号文件，需要添加到数据库")
                for phone in to_add:
                    self._append_log(f"   • {phone}")
            
            if not to_delete and not to_add:
                self._append_log("ℹ️ 账号文件与数据库同步，无需更新")
            
            # 4. 确认操作
            if to_delete or to_add:
                message = "账号状态刷新将执行以下操作：\n\n"
                if to_delete:
                    message += f"🗑️ 删除 {len(to_delete)} 个不存在的账号：\n"
                    message += "\n".join([f"   • {phone}" for phone in list(to_delete)[:5]])
                    if len(to_delete) > 5:
                        message += f"\n   ... 还有 {len(to_delete) - 5} 个"
                    message += "\n\n"
                
                if to_add:
                    message += f"📥 添加 {len(to_add)} 个新账号：\n"
                    message += "\n".join([f"   • {phone}" for phone in list(to_add)[:5]])
                    if len(to_add) > 5:
                        message += f"\n   ... 还有 {len(to_add) - 5} 个"
                    message += "\n\n"
                
                message += "然后重新检测所有账号状态。\n\n确定要继续吗？"
                
                reply = QMessageBox.question(self, "确认刷新账号状态", message)
                if reply != QMessageBox.Yes:
                    return
            
            # 5. 执行同步操作
            def worker():
                try:
                    with self.repo.session() as s:
                        from ..db.models import Account, SendLog
                        
                        # 删除不存在的账号
                        if to_delete:
                            deleted_count = 0
                            for phone in to_delete:
                                account = s.query(Account).filter(Account.phone == phone).first()
                                if account:
                                    # 删除相关的发送记录
                                    s.query(SendLog).filter(SendLog.account_id == account.id).delete()
                                    # 删除账号
                                    s.delete(account)
                                    deleted_count += 1
                            s.commit()
                            self._append_log(f"✅ 已删除 {deleted_count} 个不存在的账号")
                        
                        # 添加新账号
                        if to_add:
                            new_accounts = [acc for acc in file_accounts if acc['phone'] in to_add]
                            added_count = self.repo.upsert_accounts(new_accounts)
                            self._append_log(f"✅ 已添加 {added_count} 个新账号")
                    
                    # 6. 重新检测所有账号状态
                    self._append_log("🔍 开始检测所有账号状态...")
                    from ..core.auth import check_all_accounts
                    totals = asyncio.run(check_all_accounts(self.repo))
                    self._append_log(f"✅ 账号状态检测完成：正常 {totals.get('ok', 0)} 个，异常 {totals.get('error', 0)} 个，未授权 {totals.get('unauthorized', 0)} 个")
                    
                except Exception as e:
                    self._append_log(f"❌ 刷新账号状态失败：{e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    self.refresh()
            
            # 在后台线程中执行
            threading.Thread(target=worker, daemon=True).start()
            
        except Exception as e:
            self._append_log(f"❌ 刷新账号状态失败：{e}")
            self._show_message(f"刷新账号状态失败：{e}")

    def _reset_daily_sent_count(self):
        from PySide6.QtWidgets import QMessageBox
        
        with self.repo.session() as s:
            from ..db.models import Account
            accounts = s.query(Account).all()
        
        if not accounts:
            self._show_message("没有账号需要重置")
            return
        
        reply = QMessageBox.question(
            self, 
            "确认", 
            f"确定要重置 {len(accounts)} 个账号的今日发送计数和限制状态吗？\n（将所有账号的今日发送数量归零，并解除限制状态）"
        )
        
        if reply == QMessageBox.Yes:
            with self.repo.session() as s:
                from ..db.models import Account
                for acc in s.query(Account).all():
                    acc.daily_sent_count = 0
                    acc.last_sent_date = None
                    # 重置限制状态
                    acc.is_limited = False
                    acc.limited_until = None
                    if acc.status == "limited":
                        acc.status = "ok"
                s.commit()
            
            self._append_log(f"✅ 已重置 {len(accounts)} 个账号的今日发送计数和限制状态")
            self.refresh()

    def _update_accounts(self):
        """更新账号 - 扫描协议号文件夹并添加新账号，然后自动检测状态"""
        from ..core.syncer import read_accounts_from_files
        import threading
        import asyncio
        
        self._append_log("📥 正在扫描协议号文件夹...")
        
        try:
            # 读取账号文件
            accounts = read_accounts_from_files()
            
            if not accounts:
                self._append_log("⚠️ 未找到任何账号文件")
                self._show_message("未找到任何账号文件，请检查协议号文件夹")
                return
            
            # 添加到数据库
            added_count = self.repo.upsert_accounts(accounts)
            
            if added_count > 0:
                self._append_log(f"✅ 成功添加 {added_count} 个新账号")
                self._append_log("🔍 正在自动检测新账号状态...")
                
                # 自动检测新账号状态
                def check_new_accounts():
                    try:
                        from ..core.auth import check_all_accounts
                        totals = asyncio.run(check_all_accounts(self.repo))
                        self._append_log(f"✅ 账号状态检测完成：正常 {totals.get('ok', 0)} 个，异常 {totals.get('error', 0)} 个，未授权 {totals.get('unauthorized', 0)} 个")
                        self._show_message(f"成功添加 {added_count} 个新账号并检测状态完成")
                    except Exception as e:
                        self._append_log(f"❌ 自动检测账号状态失败：{e}")
                        self._show_message(f"成功添加 {added_count} 个新账号，但状态检测失败：{e}")
                    finally:
                        self.refresh()
                
                # 在后台线程中检测状态
                threading.Thread(target=check_new_accounts, daemon=True).start()
            else:
                self._append_log("ℹ️ 没有新账号需要添加")
                self._show_message("没有新账号需要添加，所有账号文件已存在")
                
                # 即使没有新账号，也刷新一下状态
                self._append_log("🔍 刷新现有账号状态...")
                def refresh_existing_accounts():
                    try:
                        from ..core.auth import check_all_accounts
                        totals = asyncio.run(check_all_accounts(self.repo))
                        self._append_log(f"✅ 账号状态刷新完成：正常 {totals.get('ok', 0)} 个，异常 {totals.get('error', 0)} 个，未授权 {totals.get('unauthorized', 0)} 个")
                    except Exception as e:
                        self._append_log(f"❌ 刷新账号状态失败：{e}")
                    finally:
                        self.refresh()
                
                threading.Thread(target=refresh_existing_accounts, daemon=True).start()
            
        except Exception as e:
            error_msg = f"❌ 更新账号失败: {e}"
            self._append_log(error_msg)
            self._show_message(f"更新账号失败: {e}")
            import traceback
            traceback.print_exc()

    def _delete_selected_accounts(self):
        """删除选中的账号"""
        from PySide6.QtWidgets import QMessageBox
        
        # 获取选中的行
        selected_rows = set()
        for item in self.table_accounts.selectedItems():
            selected_rows.add(item.row())
        
        if not selected_rows:
            self._show_message("请先选择要删除的账号")
            return
        
        # 获取选中的账号信息
        selected_accounts = []
        for row in selected_rows:
            phone_item = self.table_accounts.item(row, 1)  # 手机号列
            if phone_item:
                selected_accounts.append(phone_item.text())
        
        if not selected_accounts:
            self._show_message("无法获取选中账号信息")
            return
        
        # 确认删除
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除以下 {len(selected_accounts)} 个账号吗？\n\n" +
            "\n".join([f"• {phone}" for phone in selected_accounts]) +
            "\n\n⚠️ 注意：删除账号会同时删除相关的发送记录，此操作不可恢复！",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        try:
            deleted_count = 0
            with self.repo.session() as s:
                from ..db.models import Account, SendLog, Target
                
                for phone in selected_accounts:
                    # 查找账号
                    account = s.query(Account).filter(Account.phone == phone).first()
                    if account:
                        # 删除相关的发送记录
                        s.query(SendLog).filter(SendLog.account_id == account.id).delete()
                        
                        # 删除账号
                        s.delete(account)
                        deleted_count += 1
                        self._append_log(f"🗑️ 已删除账号: {phone}")
                
                s.commit()
            
            if deleted_count > 0:
                self._append_log(f"✅ 成功删除 {deleted_count} 个账号")
                self._show_message(f"成功删除 {deleted_count} 个账号")
                self.refresh()
            else:
                self._show_message("没有找到要删除的账号")
                
        except Exception as e:
            self._append_log(f"❌ 删除账号失败: {e}")
            self._show_message(f"删除账号失败: {e}")

    def _debug_accounts(self):
        """调试账号文件读取和路径问题"""
        try:
            from ..utils import PathManager, get_accounts_dir
            from ..core.syncer import read_accounts_from_files
            import json
            from pathlib import Path
            
            self._append_log("🔍 开始调试账号文件...")
            
            # 1. 检查项目根目录
            root_path = PathManager.get_root()
            self._append_log(f"📁 项目根目录: {root_path}")
            self._append_log(f"📁 根目录是否存在: {root_path.exists()}")
            
            # 2. 检查协议号目录
            accounts_dir = get_accounts_dir()
            self._append_log(f"📁 协议号目录: {accounts_dir}")
            self._append_log(f"📁 协议号目录是否存在: {accounts_dir.exists()}")
            
            if accounts_dir.exists():
                # 3. 列出协议号目录中的所有文件
                all_files = list(accounts_dir.iterdir())
                self._append_log(f"📁 协议号目录中的文件数量: {len(all_files)}")
                for file in all_files:
                    self._append_log(f"   • {file.name} ({'文件' if file.is_file() else '目录'})")
                
                # 4. 检查JSON文件
                json_files = list(accounts_dir.glob("*.json"))
                self._append_log(f"📁 JSON文件数量: {len(json_files)}")
                for json_file in json_files:
                    self._append_log(f"   • {json_file.name}")
                    try:
                        content = json_file.read_text(encoding="utf-8")
                        data = json.loads(content)
                        self._append_log(f"     - 文件大小: {len(content)} 字节")
                        self._append_log(f"     - JSON键: {list(data.keys())}")
                        if 'phone' in data:
                            self._append_log(f"     - phone字段: {data['phone']}")
                        else:
                            self._append_log(f"     - ⚠️ 缺少phone字段")
                    except Exception as e:
                        self._append_log(f"     - ❌ 读取失败: {e}")
                
                # 5. 检查session文件
                session_files = list(accounts_dir.glob("*.session"))
                self._append_log(f"📁 Session文件数量: {len(session_files)}")
                for session_file in session_files:
                    self._append_log(f"   • {session_file.name} ({session_file.stat().st_size} 字节)")
            
            # 6. 测试read_accounts_from_files函数
            self._append_log("🔍 测试read_accounts_from_files函数...")
            accounts = read_accounts_from_files()
            self._append_log(f"📁 读取到的账号数量: {len(accounts)}")
            for i, acc in enumerate(accounts):
                self._append_log(f"   {i+1}. 手机号: {acc.get('phone')}, 会话文件: {acc.get('session_file')}")
            
            # 7. 检查数据库中的账号
            try:
                with self.repo.session() as s:
                    from ..db.models import Account
                    db_accounts = s.query(Account).all()
                    self._append_log(f"📁 数据库中的账号数量: {len(db_accounts)}")
                    for acc in db_accounts:
                        self._append_log(f"   • {acc.phone} (状态: {acc.status})")
            except Exception as e:
                self._append_log(f"❌ 查询数据库失败: {e}")
            
            self._append_log("✅ 调试完成")
            
        except Exception as e:
            self._append_log(f"❌ 调试失败: {e}")
            import traceback
            traceback.print_exc()

    def _reset_target_status(self):
        from PySide6.QtWidgets import QMessageBox
        
        # 统计所有非待发送状态的目标
        with self.repo.session() as s:
            from ..db.models import Target
            # 查询所有非 pending 状态的目标
            non_pending_targets = s.query(Target).filter(Target.status != "pending").all()
            
            # 按状态分类统计
            status_counts = {}
            for t in non_pending_targets:
                status = t.status or "unknown"
                status_counts[status] = status_counts.get(status, 0) + 1
            
            total_count = len(non_pending_targets)
        
        if total_count == 0:
            self._show_message("没有需要重置的目标（所有目标都是待发送状态）")
            return
        
        # 构建状态统计文本
        status_text = ", ".join([f"{status}: {count}个" for status, count in status_counts.items()])
        
        reply = QMessageBox.question(
            self, 
            "确认重置状态", 
            f"找到 {total_count} 个非待发送状态的目标：\n{status_text}\n\n确定要将这些目标重置为待发送状态吗？"
        )
        
        if reply == QMessageBox.Yes:
            with self.repo.session() as s:
                from ..db.models import Target
                # 将所有非 pending 状态的目标重置为待发送
                updated = s.query(Target).filter(Target.status != "pending").update({
                    "status": "pending",
                    "last_sent_at": None,
                    "fail_reason": None
                })
                s.commit()
            
            self._append_log(f"✅ 已重置 {updated} 个目标为待发送状态（包括：{status_text}）")
            self._targets_page = 0
            self.refresh()

    def _add_targets(self):
        dlg = AddTargetsDialog(self)
        if dlg.exec():
            targets = dlg.get_targets()
            if targets:
                added = self.repo.upsert_targets(targets, source="manual")
                self._append_log(f"成功添加 {added} 个新目标（共输入 {len(targets)} 个，已自动去重）")
                self.refresh()
            else:
                self._show_message("未输入任何目标")

    def _delete_target(self):
        row = self.table_targets.currentRow()
        if row < 0:
            self._show_message("请先选择要删除的目标")
            return
        target_id = int(self.table_targets.item(row, 0).text())
        with self.repo.session() as s:
            from ..db.models import Target
            t = s.get(Target, target_id)
            if t:
                s.delete(t)
                s.commit()
        self.refresh()

    def _clear_sent_targets(self):
        from PySide6.QtWidgets import QMessageBox
        
        # 统计所有已处理的目标（包括发送成功和发送失败）
        with self.repo.session() as s:
            from ..db.models import Target
            # 查询所有非 pending 状态的目标（已发送、发送失败等）
            processed_targets = s.query(Target).filter(Target.status != "pending").all()
            sent_count = len([t for t in processed_targets if t.status == "sent"])
            failed_count = len([t for t in processed_targets if t.status == "failed"])
            total_count = len(processed_targets)
        
        if total_count == 0:
            self._show_message("没有已处理的目标需要清空")
            return
        
        status_text = f"已发送: {sent_count} 个，发送失败: {failed_count} 个"
        reply = QMessageBox.question(
            self, 
            "确认", 
            f"找到 {total_count} 个已处理的目标（{status_text}），确定要清空吗？"
        )
        
        if reply == QMessageBox.Yes:
            with self.repo.session() as s:
                from ..db.models import Target
                # 删除所有非 pending 状态的目标
                deleted = s.query(Target).filter(Target.status != "pending").delete(synchronize_session=False)
                s.commit()
            
            self._append_log(f"已清空 {deleted} 个已处理目标（包括发送成功和失败）")
            self._targets_page = 0
            self.refresh()

    def _clear_all_targets(self):
        from PySide6.QtWidgets import QMessageBox
        
        # 统计所有目标
        with self.repo.session() as s:
            from ..db.models import Target
            total_count = s.query(Target).count()
        
        if total_count == 0:
            self._show_message("没有目标需要清空")
            return
        
        reply = QMessageBox.question(
            self, 
            "确认", 
            f"共有 {total_count} 个群发目标，确定要全部清空吗？\n（此操作不可恢复）"
        )
        
        if reply == QMessageBox.Yes:
            with self.repo.session() as s:
                from ..db.models import Target
                deleted = s.query(Target).delete(synchronize_session=False)
                s.commit()
            
            self._append_log(f"已清空全部 {deleted} 个目标")
            self._targets_page = 0
            self.refresh()

    def _add_groups(self):
        """批量添加群组"""
        from .add_groups_dialog import AddGroupsDialog
        dlg = AddGroupsDialog(self)
        if dlg.exec():
            groups = dlg.get_groups()
            if groups:
                added = self.repo.upsert_groups(groups)
                self._append_log(f"成功添加 {added} 个新群组（共输入 {len(groups)} 个，已自动去重）")
                self.refresh()
            else:
                self._show_message("未输入任何群组")

    def _delete_group(self):
        row = self.table_groups.currentRow()
        if row < 0:
            self._show_message("请先选择要删除的群组")
            return
        group_id = int(self.table_groups.item(row, 0).text())
        with self.repo.session() as s:
            from ..db.models import Group
            g = s.get(Group, group_id)
            if g:
                s.delete(g)
                s.commit()
        self.refresh()

    def _clear_fetched_groups(self):
        """清空已获取的群组"""
        from PySide6.QtWidgets import QMessageBox
        
        # 统计已获取的群组
        with self.repo.session() as s:
            from ..db.models import Group
            fetched_count = s.query(Group).filter(Group.fetched == True).count()
        
        if fetched_count == 0:
            self._show_message("没有已获取的群组需要清空")
            return
        
        reply = QMessageBox.question(
            self, 
            "确认", 
            f"找到 {fetched_count} 个已获取的群组，确定要清空吗？"
        )
        
        if reply == QMessageBox.Yes:
            with self.repo.session() as s:
                from ..db.models import Group
                deleted = s.query(Group).filter(Group.fetched == True).delete(synchronize_session=False)
                s.commit()
            
            self._append_log(f"已清空 {deleted} 个已获取的群组")
            self._groups_page = 0
            self.refresh()

    def _clear_all_groups(self):
        """清空所有群组"""
        from PySide6.QtWidgets import QMessageBox
        
        # 统计所有群组
        with self.repo.session() as s:
            from ..db.models import Group
            total_count = s.query(Group).count()
        
        if total_count == 0:
            self._show_message("没有群组需要清空")
            return
        
        reply = QMessageBox.question(
            self, 
            "确认", 
            f"共有 {total_count} 个群组，确定要全部清空吗？\n（此操作不可恢复）"
        )
        
        if reply == QMessageBox.Yes:
            with self.repo.session() as s:
                from ..db.models import Group
                deleted = s.query(Group).delete(synchronize_session=False)
                s.commit()
            
            self._append_log(f"已清空全部 {deleted} 个群组")
            self._groups_page = 0
            self.refresh()

    def _reset_groups(self):
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(self, "确认", "确定要重置所有群组状态吗？")
        if reply == QMessageBox.Yes:
            with self.repo.session() as s:
                from ..db.models import Group
                s.query(Group).update({"joined": False, "fetched": False, "last_fetched_at": None})
                s.commit()
            self.refresh()


