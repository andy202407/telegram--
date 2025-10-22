"""
工具函数模块
"""

import sys
from pathlib import Path


class PathManager:
    """全局路径管理器"""
    _root_path = None
    
    @classmethod
    def set_root(cls, root_path: str):
        """设置项目根目录"""
        cls._root_path = Path(root_path).resolve()
    
    @classmethod
    def get_root(cls) -> Path:
        """获取项目根目录"""
        if cls._root_path is None:
            # 开发环境：返回项目根目录
            # 打包环境：抛出异常，强制用户配置
            if getattr(sys, 'frozen', False):
                raise RuntimeError("项目根目录未配置，请在UI中手动设置")
            else:
                # 开发环境自动识别
                current_file = Path(__file__)
                cls._root_path = current_file.parent.parent
        return cls._root_path
    
    @classmethod
    def get_path(cls, relative_path: str) -> Path:
        """获取基于项目根目录的完整路径"""
        return cls.get_root() / relative_path


def get_resource_path(relative_path: str) -> Path:
    """
    获取资源文件的绝对路径
    支持开发环境和打包后的环境
    
    Args:
        relative_path: 相对于项目根目录的路径
        
    Returns:
        资源文件的绝对路径
    """
    return PathManager.get_path(relative_path)


def get_accounts_dir() -> Path:
    """获取账号目录路径"""
    try:
        return PathManager.get_path("协议号")
    except RuntimeError:
        # 项目根目录未配置时，返回一个临时路径
        return Path("协议号")


def get_targets_file() -> Path:
    """获取发送目标文件路径"""
    try:
        return PathManager.get_path("群发目标/user.txt")
    except RuntimeError:
        # 项目根目录未配置时，返回一个临时路径
        return Path("群发目标/user.txt")


def get_groups_file() -> Path:
    """获取群组文件路径"""
    try:
        return PathManager.get_path("群/group.txt")
    except RuntimeError:
        # 项目根目录未配置时，返回一个临时路径
        return Path("群/group.txt")


def get_data_dir() -> Path:
    """获取数据目录路径"""
    try:
        return PathManager.get_path("data")
    except RuntimeError:
        # 项目根目录未配置时，返回一个临时路径
        return Path("data")


def get_assets_dir() -> Path:
    """获取资源目录路径"""
    try:
        return PathManager.get_path("assets")
    except RuntimeError:
        # 项目根目录未配置时，返回一个临时路径
        return Path("assets")


def get_db_path() -> Path:
    """获取数据库文件路径"""
    try:
        return PathManager.get_path("data/app.db")
    except RuntimeError:
        # 项目根目录未配置时，返回一个临时路径
        return Path("data/app.db")


def ensure_directories():
    """确保必要的目录存在"""
    directories = [
        get_accounts_dir(),
        PathManager.get_path("群发目标"),
        PathManager.get_path("群"),
        get_data_dir(),
    ]
    
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
