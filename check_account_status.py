#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查账号状态
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# 添加项目路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.db.repo import Repo
from src.db.models import Account

def check_account_status():
    """检查账号状态"""
    print("🔍 检查账号状态")
    print("=" * 50)
    
    # 初始化数据库
    from src.utils import get_db_path
    db_path = get_db_path()
    repo = Repo(db_path)
    
    with repo.session() as s:
        accounts = s.query(Account).all()
        
        print(f"📊 账号状态统计:")
        print(f"   总账号数: {len(accounts)}")
        
        status_count = {}
        limited_count = 0
        
        for acc in accounts:
            # 统计状态
            status = acc.status or "unknown"
            status_count[status] = status_count.get(status, 0) + 1
            
            # 检查限制状态
            if acc.is_limited:
                limited_count += 1
                if acc.limited_until:
                    remaining = acc.limited_until - datetime.utcnow()
                    if remaining.total_seconds() > 0:
                        hours = remaining.total_seconds() / 3600
                        print(f"   ⏰ {acc.phone}: 限制中，剩余 {hours:.1f} 小时")
                    else:
                        print(f"   ✅ {acc.phone}: 限制期已过，可恢复")
                else:
                    print(f"   ⚠️ {acc.phone}: 限制中，无到期时间")
        
        print(f"\n📈 状态分布:")
        for status, count in status_count.items():
            print(f"   {status}: {count} 个")
        
        print(f"\n🚫 限制状态:")
        print(f"   被限制账号: {limited_count} 个")
        print(f"   正常账号: {len(accounts) - limited_count} 个")
        
        # 检查特定账号
        target_phone = "918882623881"
        target_acc = s.query(Account).filter(Account.phone == target_phone).first()
        
        if target_acc:
            print(f"\n🎯 目标账号 {target_phone} 详情:")
            print(f"   状态: {target_acc.status}")
            print(f"   是否限制: {target_acc.is_limited}")
            print(f"   限制到期: {target_acc.limited_until}")
            print(f"   今日发送: {target_acc.daily_sent_count}")
            print(f"   总发送: {target_acc.total_sent_count}")
            
            if target_acc.is_limited and target_acc.limited_until:
                remaining = target_acc.limited_until - datetime.utcnow()
                if remaining.total_seconds() > 0:
                    hours = remaining.total_seconds() / 3600
                    print(f"   ⏰ 剩余限制时间: {hours:.1f} 小时")
                else:
                    print(f"   ✅ 限制期已过，可以重置状态")
        else:
            print(f"\n❌ 未找到账号 {target_phone}")

if __name__ == "__main__":
    check_account_status()
