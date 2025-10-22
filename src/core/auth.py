from __future__ import annotations

from datetime import datetime
from typing import Optional
import pytz

from sqlalchemy import select

from ..db.repo import Repo
from ..db.models import Account
from ..login import login_account_by_phone


async def check_and_update_account(repo: Repo, phone: str) -> bool:
    """Try to login the account; update status and last_login_at in DB.
    Returns True if authorized, False otherwise.
    """
    # 获取上海时间
    shanghai_tz = pytz.timezone('Asia/Shanghai')
    current_time = datetime.now(shanghai_tz)
    
    try:
        client, _ = await login_account_by_phone(phone)
        try:
            me = await client.get_me()
            ok = me is not None
        finally:
            await client.disconnect()
        status = "ok" if ok else "unauthorized"
    except Exception as e:
        ok = False
        status = f"error"
        # 添加详细错误日志
        print(f"❌ 账号 {phone} 登录失败: {e}")
        import traceback
        traceback.print_exc()

    with repo.session() as s:
        acc = s.execute(select(Account).where(Account.phone == phone)).scalar_one_or_none()
        if acc:
            acc.status = status
            # 无论登录成功与否，都更新最后登录时间
            acc.last_login_at = current_time
            s.commit()
    return ok


async def check_all_accounts(repo: Repo) -> dict:
    """Iterate all accounts and update their status."""
    results: dict[str, bool] = {}
    totals = {"ok": 0, "error": 0, "unauthorized": 0}
    
    with repo.session() as s:
        phones = [a.phone for a in s.query(Account).all()]
    
    for phone in phones:
        success = await check_and_update_account(repo, phone)
        results[phone] = success
        
        # 统计结果
        with repo.session() as s:
            acc = s.execute(select(Account).where(Account.phone == phone)).scalar_one_or_none()
            if acc:
                if acc.status == "ok":
                    totals["ok"] += 1
                elif acc.status == "unauthorized":
                    totals["unauthorized"] += 1
                else:
                    totals["error"] += 1
    
    return totals


