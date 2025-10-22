# monterey_iso_builder.py
# 作用：在 Windows 上把 Apple 的 InstallAssistant.pkg 提取为 Monterey.iso（可用于 QEMU）
# 依赖外部工具：bsdtar.exe、dmg2img.exe（脚本会自动在 PATH 中找，或你可用参数手动指定）
# 用法示例：
#   python monterey_iso_builder.py --pkg "E:\Downloads\InstallAssistant.pkg" --out "E:\macOS\Monterey.iso"
# 可选参数：
#   --work "E:\tmp\monterey_work"    指定工作目录（默认自动创建临时目录）
#   --bsdtar "D:\macTools\bsdtar.exe" 指定 bsdtar 路径
#   --dmg2img "D:\macTools\dmg2img.exe" 指定 dmg2img 路径
#   --keep                         保留工作目录，不自动清理

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

def which(cmd):
    # Windows 上支持 .exe 自动搜索
    from shutil import which as _which
    return _which(cmd)

def run(cmd, cwd=None):
    print(f"[RUN] {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=cwd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] 命令执行失败，退出码：{e.returncode}")
        sys.exit(1)

def find_file(root_dir, names):
    # 在 root_dir 下递归查找 names（列表）中的任意一个文件名
    names_lower = [n.lower() for n in names]
    for base, _, files in os.walk(root_dir):
        for f in files:
            if f.lower() in names_lower:
                return os.path.join(base, f)
    return None

def main():
    parser = argparse.ArgumentParser(description="Convert InstallAssistant.pkg -> Monterey.iso (Windows)")
    parser.add_argument("--pkg", required=True, help="InstallAssistant.pkg 路径")
    parser.add_argument("--out", required=True, help="输出 ISO 路径，如 E:\\macOS\\Monterey.iso")
    parser.add_argument("--work", default=None, help="工作目录（可选）")
    parser.add_argument("--bsdtar", default=None, help="bsdtar.exe 路径（可选）")
    parser.add_argument("--dmg2img", default=None, help="dmg2img.exe 路径（可选）")
    parser.add_argument("--keep", action="store_true", help="保留工作目录以便排错（默认自动删除）")
    args = parser.parse_args()

    pkg_path = os.path.abspath(args.pkg)
    out_iso = os.path.abspath(args.out)

    if not os.path.isfile(pkg_path):
        print(f"[ERROR] 找不到 pkg：{pkg_path}")
        sys.exit(1)

    # 解析工具路径
    bsdtar = args.bsdtar or which("bsdtar") or which("bsdtar.exe")
    dmg2img = args.dmg2img or which("dmg2img") or which("dmg2img.exe")

    if not bsdtar or not os.path.exists(bsdtar):
        print("[ERROR] 未找到 bsdtar.exe。请安装后加入 PATH 或用 --bsdtar 指定路径。")
        sys.exit(1)
    if not dmg2img or not os.path.exists(dmg2img):
        print("[ERROR] 未找到 dmg2img.exe。请安装后加入 PATH 或用 --dmg2img 指定路径。")
        sys.exit(1)

    # 工作目录
    workdir = args.work or tempfile.mkdtemp(prefix="monterey_iso_")
    workdir = os.path.abspath(workdir)
    os.makedirs(workdir, exist_ok=True)
    print(f"[INFO] 工作目录：{workdir}")

    pkg_out = os.path.join(workdir, "pkg")
    payload_out = os.path.join(workdir, "payload")
    os.makedirs(pkg_out, exist_ok=True)
    os.makedirs(payload_out, exist_ok=True)

    try:
        # 1) 解 InstallAssistant.pkg
        run([bsdtar, "-xf", pkg_path, "-C", pkg_out])

        # 2) 找 Payload 或 Payload~（两种命名之一）
        candidate = None
        for name in ("Payload~", "Payload"):
            p = os.path.join(pkg_out, name)
            if os.path.exists(p):
                candidate = p
                break
        if not candidate:
            # 某些版本放在子 pkg 里，兜底：全盘找 Payload~/Payload
            candidate = find_file(pkg_out, ["Payload~", "Payload"])
            if not candidate:
                print("[ERROR] 没找到 Payload~/Payload（InstallAssistant 结构异常或解包失败）。")
                sys.exit(1)

        # 3) 解 Payload 到 payload_out
        run([bsdtar, "-xf", candidate, "-C", payload_out])

        # 4) 定位 SharedSupport 中的 InstallESD.dmg
        shared_support = os.path.join(
            payload_out,
            "Applications",
            "Install macOS Monterey.app",
            "Contents",
            "SharedSupport"
        )

        if not os.path.isdir(shared_support):
            # 兜底：递归找 InstallESD.dmg
            print("[WARN] 预期的 SharedSupport 目录不存在，尝试递归搜索 InstallESD.dmg ...")
            install_esd = find_file(payload_out, ["InstallESD.dmg"])
        else:
            install_esd = os.path.join(shared_support, "InstallESD.dmg")
            if not os.path.isfile(install_esd):
                # 某些版本名字大小写或位置差异，递归兜底
                print("[WARN] 预期路径无 InstallESD.dmg，尝试递归搜索 ...")
                install_esd = find_file(payload_out, ["InstallESD.dmg"])

        if not install_esd or not os.path.isfile(install_esd):
            print("[ERROR] 没找到 InstallESD.dmg。请确认 pkg 是否完整，或换 12.7.4 版本重试。")
            sys.exit(1)

        print(f"[INFO] 找到 InstallESD.dmg：{install_esd}")

        # 5) DMG -> ISO
        out_dir = os.path.dirname(out_iso)
        os.makedirs(out_dir, exist_ok=True)
        run([dmg2img, install_esd, out_iso])

        if os.path.isfile(out_iso):
            size_gb = os.path.getsize(out_iso) / (1024**3)
            print(f"[OK] ISO 生成成功：{out_iso}  (≈{size_gb:.2f} GB)")
            print("[NEXT] 现在可以直接把这个 ISO 用在 QEMU 的光驱引导上了。")
        else:
            print("[ERROR] 未生成 ISO 文件，请检查上面的错误输出。")
            sys.exit(1)

    finally:
        if args.keep:
            print(f"[INFO] 按要求保留工作目录：{workdir}")
        else:
            try:
                shutil.rmtree(workdir, ignore_errors=True)
                print("[CLEAN] 已清理工作目录。")
            except Exception as e:
                print(f"[WARN] 清理工作目录失败：{e}")

if __name__ == "__main__":
    main()
