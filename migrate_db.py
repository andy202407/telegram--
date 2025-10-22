#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库迁移脚本：添加每日发送统计字段
"""
import sqlite3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

def migrate_database():
    """迁移数据库，添加新字段"""
    from src.utils import get_db_path
    db_path = get_db_path()
    
    if not db_path.exists():
        print("数据库文件不存在，无需迁移")
        return
    
    print("开始数据库迁移...")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查是否已经存在新字段
        cursor.execute("PRAGMA table_info(accounts)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'daily_sent_count' in columns:
            print("字段已存在，无需迁移")
            return
        
        # 添加新字段
        if 'daily_sent_count' not in columns:
            print("添加 daily_sent_count 字段...")
            cursor.execute("ALTER TABLE accounts ADD COLUMN daily_sent_count INTEGER DEFAULT 0")
        
        if 'last_sent_date' not in columns:
            print("添加 last_sent_date 字段...")
            cursor.execute("ALTER TABLE accounts ADD COLUMN last_sent_date TEXT")
        
        if 'total_sent_count' not in columns:
            print("添加 total_sent_count 字段...")
            cursor.execute("ALTER TABLE accounts ADD COLUMN total_sent_count INTEGER DEFAULT 0")
        
        conn.commit()
        print("✅ 数据库迁移完成")
        
    except Exception as e:
        print(f"数据库迁移失败: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    migrate_database()
