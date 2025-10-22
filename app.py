import sys
import os
import warnings
from pathlib import Path

# 禁用Qt相关的警告
os.environ['QT_LOGGING_RULES'] = '*.debug=false;qt.qpa.*=false'
warnings.filterwarnings("ignore", category=DeprecationWarning)

from PySide6.QtWidgets import QApplication

from src.db.repo import Repo
from src.ui.main_window import MainWindow


def main() -> None:
    try:
        # 不再创建目录和数据库
        # 不再运行启动同步
        # 只创建UI，等待用户手动配置
        
        print("正在启动应用...")
        
        # 设置环境变量，强制使用软件渲染并禁用警告
        os.environ['QT_QUICK_BACKEND'] = 'software'
        os.environ['QT_OPENGL'] = 'software'
        os.environ['QT_GRAPHICSSYSTEM'] = 'raster'
        os.environ['QT_XCB_GL_INTEGRATION'] = 'none'
        os.environ['QT_LOGGING_RULES'] = '*.debug=false;qt.qpa.*=false;qt.qpa.backingstore.*=false'
        os.environ['QT_MESSAGE_PATTERN'] = ''
        
        # 设置Qt应用程序属性（必须在创建QApplication之前）
        from PySide6.QtCore import Qt
        # 使用软件渲染，避免硬件兼容性问题
        try:
            QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL, True)
            QApplication.setAttribute(Qt.AA_UseOpenGLES, False)
        except:
            pass
        
        app = QApplication(sys.argv)
        
        # 创建空repo，等待用户手动初始化
        repo = Repo(None)
        
        # 加载样式
        try:
            from src.utils import get_assets_dir
            style_path = get_assets_dir() / 'style.qss'
            if style_path.exists():
                app.setStyleSheet(style_path.read_text(encoding='utf-8'))
                print("样式加载成功")
        except Exception as e:
            print(f"样式加载失败: {e}")
        
        print("正在创建主窗口...")
        try:
            win = MainWindow(repo)
            print("主窗口创建成功")
            
            win.show()
            win.raise_()  # 确保窗口在最前面
            win.activateWindow()  # 激活窗口
            print("应用已启动，进入事件循环")
            print("窗口应该已显示，如果看不到请检查任务栏或最小化的窗口")
            
            # 进入事件循环
            exit_code = app.exec()
            print(f"应用正常退出，退出码: {exit_code}")
            sys.exit(exit_code)
            
        except Exception as win_e:
            print(f"创建或显示窗口失败: {win_e}")
            import traceback
            traceback.print_exc()
            input("按回车键退出...")
            sys.exit(1)
    except Exception as e:
        print(f"程序启动失败: {e}")
        import traceback
        traceback.print_exc()
        input("按回车键退出...")


if __name__ == "__main__":
    main()


