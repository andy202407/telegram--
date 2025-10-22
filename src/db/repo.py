from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Account, Target, Group, GroupMember, SendRun, SendLog, AppSettings, create_session
import json


class Repo:
    def __init__(self, db_path: Optional[Path]):
        if db_path and db_path.exists():
            self.Session = create_session(db_path)
        else:
            self.Session = None

    def session(self) -> Session:
        if self.Session is None:
            raise Exception("数据库未初始化，请先点击'初始化数据'按钮")
        return self.Session()
    
    def _ensure_send_status_field(self):
        """确保accounts表有send_status字段"""
        try:
            with self.session() as s:
                # 检查字段是否已存在
                result = s.execute(text("PRAGMA table_info(accounts)"))
                columns = [row[1] for row in result.fetchall()]
                
                if 'send_status' not in columns:
                    print("正在添加 send_status 字段...")
                    s.execute(text("ALTER TABLE accounts ADD COLUMN send_status VARCHAR(32) DEFAULT '未启用'"))
                    s.commit()
                    print("✅ send_status 字段添加成功")
                
                # 更新现有账号的发送状态
                s.execute(text("UPDATE accounts SET send_status = '未启用' WHERE send_status IS NULL OR send_status = ''"))
                s.commit()
        except Exception as e:
            print(f"⚠️ 添加send_status字段失败: {e}")

    # accounts
    def upsert_accounts(self, items: list[dict]) -> int:
        inserted = 0
        with self.session() as s:
            for it in items:
                phone = str(it.get("phone"))
                session_file = it.get("session_file")
                if not phone:
                    continue
                existing = s.execute(select(Account).where(Account.phone == phone)).scalar_one_or_none()
                if existing:
                    if session_file and existing.session_file != session_file:
                        existing.session_file = session_file
                else:
                    s.add(Account(phone=phone, session_file=session_file, status="unknown"))
                    inserted += 1
            s.commit()
        return inserted

    # targets
    def upsert_targets(self, identifiers: Iterable[str], source: str = "file") -> int:
        inserted = 0
        with self.session() as s:
            for ident in identifiers:
                ident = ident.strip()
                if not ident:
                    continue
                exists = s.execute(select(Target).where(Target.identifier == ident)).scalar_one_or_none()
                if not exists:
                    s.add(Target(identifier=ident, source=source, status="pending"))
                    inserted += 1
            s.commit()
        return inserted

    # groups
    def upsert_groups(self, links: Iterable[str]) -> int:
        inserted = 0
        with self.session() as s:
            for link in links:
                link = link.strip()
                if not link:
                    continue
                exists = s.execute(select(Group).where(Group.link_or_username == link)).scalar_one_or_none()
                if not exists:
                    s.add(Group(link_or_username=link, joined=False, fetched=False))
                    inserted += 1
            s.commit()
        return inserted

    # fetch results
    def add_group_members(self, group_id: int, members: list[dict]) -> int:
        inserted = 0
        with self.session() as s:
            for m in members:
                # 先检查是否已存在
                existing = s.execute(
                    select(GroupMember).where(
                        GroupMember.group_id == group_id,
                        GroupMember.identifier == m["identifier"]
                    )
                ).scalar_one_or_none()
                
                if not existing:
                    try:
                        s.add(
                            GroupMember(
                                group_id=group_id,
                                identifier=m["identifier"],
                                is_bot=bool(m.get("is_bot", False)),
                                is_deleted=bool(m.get("is_deleted", False)),
                            )
                        )
                        inserted += 1
                    except IntegrityError:
                        s.rollback()
                        continue
            s.commit()
        return inserted

    # settings persistence
    def save_setting(self, key: str, value: any) -> None:
        try:
            with self.session() as s:
                existing = s.execute(select(AppSettings).where(AppSettings.key == key)).scalar_one_or_none()
                if existing:
                    existing.value = json.dumps(value)
                else:
                    s.add(AppSettings(key=key, value=json.dumps(value)))
                s.commit()
                print(f"✅ 设置已保存到数据库: {key} = {value}")
        except Exception as e:
            # 如果表不存在或其他数据库错误，忽略保存操作
            print(f"❌ 保存设置失败: {key} = {value}, 错误: {e}")
            pass

    def load_setting(self, key: str, default: any = None) -> any:
        try:
            with self.session() as s:
                existing = s.execute(select(AppSettings).where(AppSettings.key == key)).scalar_one_or_none()
                if existing and existing.value:
                    try:
                        result = json.loads(existing.value)
                        print(f"✅ 设置已从数据库加载: {key} = {result}")
                        return result
                    except Exception as e:
                        print(f"❌ 解析设置失败: {key}, 错误: {e}")
                        return default
                print(f"⚠️ 设置不存在: {key}, 返回默认值: {default}")
                return default
        except Exception as e:
            # 如果表不存在或其他数据库错误，返回默认值
            print(f"❌ 加载设置失败: {key}, 错误: {e}, 返回默认值: {default}")
            return default


