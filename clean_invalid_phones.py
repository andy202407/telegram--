#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清理无效手机号格式的目标
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from src.db.repo import Repo
from src.db.models import Target

def clean_invalid_phone_numbers():
    """清理无效手机号格式的目标"""
    print("🧹 清理无效手机号格式的目标")
    print("=" * 50)
    
    # 初始化数据库
    from src.utils import get_db_path
    db_path = get_db_path()
    repo = Repo(db_path)
    
    try:
        with repo.session() as s:
            # 查找所有目标
            targets = s.query(Target).all()
            
            print(f"📋 找到 {len(targets)} 个目标")
            
            invalid_count = 0
            valid_count = 0
            
            for target in targets:
                identifier = target.identifier
                
                # 检查是否是手机号格式（纯数字且不以+开头）
                if identifier.isdigit() and not identifier.startswith('+'):
                    print(f"❌ 发现无效手机号: {identifier}")
                    invalid_count += 1
                    
                    # 删除无效目标
                    s.delete(target)
                else:
                    valid_count += 1
            
            # 提交删除操作
            s.commit()
            
            print(f"\n📊 清理结果:")
            print(f"   - 有效目标: {valid_count} 个")
            print(f"   - 删除无效手机号: {invalid_count} 个")
            
            if invalid_count > 0:
                print(f"\n✅ 已清理 {invalid_count} 个无效手机号格式的目标")
            else:
                print(f"\n✅ 没有发现无效手机号格式的目标")
                
    except Exception as e:
        print(f"❌ 清理失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    clean_invalid_phone_numbers()
