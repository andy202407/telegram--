from __future__ import annotations

from pathlib import Path
import json

from ..db.repo import Repo
from ..utils import get_accounts_dir, get_targets_file, get_groups_file


def read_accounts_from_files() -> list[dict]:
    items: list[dict] = []
    accounts_dir = get_accounts_dir()
    if not accounts_dir.exists():
        return items
    for p in sorted(accounts_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # 支持多种phone字段格式
            phone = None
            if 'phone' in data:
                phone = str(data.get("phone"))
            elif 'phone_number' in data:
                phone = str(data.get("phone_number"))
            
            # 清理电话号码格式（移除空格、+号等）
            if phone:
                phone = phone.replace(" ", "").replace("+", "").replace("-", "")
            
            session_file = data.get("session_file") or phone
            if phone:
                # 如果session_file是绝对路径，提取文件名
                if session_file and "/" in session_file or "\\" in session_file:
                    from pathlib import Path
                    session_file = Path(session_file).name
                items.append({"phone": phone, "session_file": session_file})
        except Exception:
            continue
    return items


def read_targets_from_file() -> list[str]:
    targets_file = get_targets_file()
    if not targets_file.exists():
        return []
    lines = [x.strip() for x in targets_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    return lines


def read_groups_from_file() -> list[str]:
    groups_file = get_groups_file()
    if not groups_file.exists():
        return []
    lines = [x.strip() for x in groups_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    return lines


def run_startup_sync(repo: Repo) -> dict:
    acc = read_accounts_from_files()
    tgs = read_targets_from_file()
    gps = read_groups_from_file()

    a = repo.upsert_accounts(acc)
    t = repo.upsert_targets(tgs, source="file")
    g = repo.upsert_groups(gps)

    return {"accounts_new": a, "targets_new": t, "groups_new": g}


async def run_startup_account_check(repo: Repo) -> dict:
    """启动时自动检测账号状态"""
    try:
        from .auth import check_all_accounts
        
        print("🔍 正在检测账号状态...")
        totals = await check_all_accounts(repo)
        print(f"✅ 账号状态检测完成：正常 {totals.get('ok', 0)} 个，异常 {totals.get('error', 0)} 个，未授权 {totals.get('unauthorized', 0)} 个")
        
        return totals
    except Exception as e:
        print(f"⚠️ 账号状态检测失败: {e}")
        return {"ok": 0, "error": 0, "unauthorized": 0}


