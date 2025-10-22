#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram群发应用打包脚本
使用PyInstaller将应用打包成可执行文件
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

def check_pyinstaller():
    """检查PyInstaller是否已安装"""
    try:
        import PyInstaller
        print(f"✅ PyInstaller已安装，版本: {PyInstaller.__version__}")
        return True
    except ImportError:
        print("❌ PyInstaller未安装，正在安装...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
            print("✅ PyInstaller安装成功")
            return True
        except subprocess.CalledProcessError:
            print("❌ PyInstaller安装失败")
            return False

def clean_build_dirs():
    """清理之前的构建目录"""
    dirs_to_clean = ["build", "dist", "__pycache__"]
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)
            print(f"🧹 清理目录: {dir_name}")
    
    # 清理spec文件
    spec_files = list(Path(".").glob("*.spec"))
    for spec_file in spec_files:
        spec_file.unlink()
        print(f"🧹 清理文件: {spec_file}")

def create_spec_file():
    """创建PyInstaller spec文件"""
    spec_content = '''# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets', 'assets'),
        ('data', 'data'),
        ('协议号', '协议号'),
        ('群', '群'),
        ('群发目标', '群发目标'),
    ],
    hiddenimports=[
        'telethon',
        'telethon.tl',
        'telethon.tl.types',
        'telethon.tl.functions',
        'telethon.errors',
        'sqlalchemy',
        'sqlalchemy.orm',
        'sqlalchemy.ext.declarative',
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtWidgets',
        'PySide6.QtGui',
        'pytz',
        'asyncio',
        'cryptg',
        'hachoir',
        'PIL',
        'PIL.Image',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Telegram群发工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if os.path.exists('assets/icon.ico') else None,
)
'''
    
    with open('telegram_broadcast.spec', 'w', encoding='utf-8') as f:
        f.write(spec_content)
    print("📝 创建spec文件: telegram_broadcast.spec")

def create_icon():
    """创建应用图标（如果不存在）"""
    icon_path = Path("assets/icon.ico")
    if not icon_path.exists():
        print("⚠️ 未找到图标文件，将使用默认图标")
        # 创建一个简单的图标文件（可选）
        try:
            from PIL import Image, ImageDraw
            # 创建一个简单的图标
            img = Image.new('RGBA', (64, 64), (0, 123, 255, 255))
            draw = ImageDraw.Draw(img)
            draw.text((10, 20), "TG", fill=(255, 255, 255, 255))
            
            # 确保assets目录存在
            Path("assets").mkdir(exist_ok=True)
            img.save(icon_path, format='ICO', sizes=[(64, 64), (32, 32), (16, 16)])
            print(f"🎨 创建图标文件: {icon_path}")
        except ImportError:
            print("⚠️ PIL未安装，跳过图标创建")

def build_app():
    """构建应用"""
    print("🔨 开始构建应用...")
    
    try:
        # 使用spec文件构建
        cmd = [sys.executable, "-m", "PyInstaller", "telegram_broadcast.spec"]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        
        if result.returncode == 0:
            print("✅ 构建成功！")
            return True
        else:
            print("❌ 构建失败！")
            print("错误输出:", result.stderr)
            return False
            
    except Exception as e:
        print(f"❌ 构建过程出错: {e}")
        return False

def create_distribution():
    """创建发布包"""
    dist_dir = Path("dist")
    if not dist_dir.exists():
        print("❌ 构建目录不存在")
        return False
    
    # 查找生成的可执行文件
    exe_files = list(dist_dir.glob("*.exe"))
    if not exe_files:
        print("❌ 未找到可执行文件")
        return False
    
    exe_file = exe_files[0]
    print(f"📦 找到可执行文件: {exe_file}")
    
    # 创建发布目录
    release_dir = Path("release")
    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir()
    
    # 复制可执行文件
    shutil.copy2(exe_file, release_dir / "Telegram群发工具.exe")
    
    # 复制必要的文件
    files_to_copy = [
        ("README.md", "使用说明.md"),
        ("requirements.txt", "requirements.txt"),
    ]
    
    for src, dst in files_to_copy:
        src_path = Path(src)
        if src_path.exists():
            shutil.copy2(src_path, release_dir / dst)
            print(f"📋 复制文件: {src} -> {dst}")
    
    # 创建启动脚本
    create_startup_script(release_dir)
    
    print(f"✅ 发布包已创建: {release_dir}")
    return True

def create_startup_script(release_dir):
    """创建启动脚本"""
    startup_script = '''@echo off
title Telegram群发工具
echo 正在启动Telegram群发工具...
echo.
"Telegram群发工具.exe"
echo.
echo 程序已退出，按任意键关闭窗口...
pause >nul
'''
    
    script_path = release_dir / "启动.bat"
    with open(script_path, 'w', encoding='gbk') as f:
        f.write(startup_script)
    print(f"🚀 创建启动脚本: {script_path}")

def main():
    """主函数"""
    print("🚀 Telegram群发工具打包脚本")
    print("=" * 50)
    
    # 检查PyInstaller
    if not check_pyinstaller():
        return False
    
    # 清理构建目录
    clean_build_dirs()
    
    # 创建图标
    create_icon()
    
    # 创建spec文件
    create_spec_file()
    
    # 构建应用
    if not build_app():
        return False
    
    # 创建发布包
    if not create_distribution():
        return False
    
    print("\n🎉 打包完成！")
    print("📁 发布文件位于: release/ 目录")
    print("🚀 运行: release/Telegram群发工具.exe")
    
    return True

if __name__ == "__main__":
    success = main()
    if not success:
        print("\n❌ 打包失败，请检查错误信息")
        input("按回车键退出...")
        sys.exit(1)
    else:
        input("\n按回车键退出...")
