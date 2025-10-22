"""
增强版群成员采集模块
支持按在线状态、活跃度等条件过滤成员
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select

from telethon.errors import FloodWaitError, InviteHashInvalidError, InviteHashExpiredError
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, GetFullChatRequest
from telethon.tl.types import Channel, Chat, User, UserStatusOnline, UserStatusOffline, UserStatusRecently, UserStatusLastWeek, UserStatusLastMonth

from ..db.repo import Repo
from ..db.models import Group, GroupMember
from ..login import login_account_by_index, login_account_by_phone
from .phone_utils import format_phone_number, is_valid_phone_number


class MemberFilter:
    """成员过滤条件"""
    
    def __init__(self, 
                 online_only: bool = False,
                 recent_online_days: int = 0,  # 最近N天在线
                 exclude_bots: bool = True,
                 exclude_deleted: bool = True,
                 min_activity_score: int = 0):  # 活跃度评分
        self.online_only = online_only
        self.recent_online_days = recent_online_days
        self.exclude_bots = exclude_bots
        self.exclude_deleted = exclude_deleted
        self.min_activity_score = min_activity_score


def _is_user_online(user: User, recent_days: int = 0) -> bool:
    """检查用户是否在线或最近在线"""
    if not hasattr(user, 'status') or user.status is None:
        return False
    
    # 当前在线
    if isinstance(user.status, UserStatusOnline):
        return True
    
    # 最近在线
    if recent_days > 0:
        if isinstance(user.status, UserStatusRecently):
            return True
        
        # 检查最后在线时间
        if isinstance(user.status, UserStatusOffline):
            if user.status.was_online:
                now = datetime.utcnow()
                cutoff = now - timedelta(days=recent_days)
                return user.status.was_online.replace(tzinfo=None) > cutoff
        
        # 本周在线
        if isinstance(user.status, UserStatusLastWeek):
            return recent_days >= 7
        
        # 本月在线
        if isinstance(user.status, UserStatusLastMonth):
            return recent_days >= 30
    
    return False


def _calculate_activity_score(user: User) -> int:
    """计算用户活跃度评分"""
    score = 0
    
    # 基础评分
    if hasattr(user, 'username') and user.username:
        score += 10  # 有用户名
    
    if hasattr(user, 'phone') and user.phone:
        score += 5   # 有手机号
    
    # 在线状态评分
    if hasattr(user, 'status') and user.status:
        if isinstance(user.status, UserStatusOnline):
            score += 50  # 当前在线
        elif isinstance(user.status, UserStatusRecently):
            score += 30  # 最近在线
        elif isinstance(user.status, UserStatusLastWeek):
            score += 20  # 本周在线
        elif isinstance(user.status, UserStatusLastMonth):
            score += 10  # 本月在线
    
    # 头像评分
    if hasattr(user, 'photo') and user.photo:
        score += 5   # 有头像
    
    return score


def _valid_user(u: User, filter_config: MemberFilter) -> bool:
    """检查用户是否符合过滤条件"""
    # 排除已删除账号
    if filter_config.exclude_deleted and getattr(u, "deleted", False):
        return False
    
    # 排除机器人
    if filter_config.exclude_bots and getattr(u, "bot", False):
        return False
    
    # 在线状态过滤
    if filter_config.online_only or filter_config.recent_online_days > 0:
        if not _is_user_online(u, filter_config.recent_online_days):
            return False
    
    # 活跃度过滤
    if filter_config.min_activity_score > 0:
        if _calculate_activity_score(u) < filter_config.min_activity_score:
            return False
    
    return True


async def _ensure_join(client, link_or_username: str) -> bool:
    """确保加入群组，返回是否成功"""
    s = (link_or_username or "").strip()
    if not s:
        return False
    
    try:
        if s.startswith("https://t.me/+") or "/+" in s:
            invite_hash = s.split("/+", 1)[-1].split("?", 1)[0]
            await client(ImportChatInviteRequest(invite_hash))
        elif s.startswith("https://t.me/"):
            username = s.split("https://t.me/", 1)[-1].split("?", 1)[0]
            await client(JoinChannelRequest(username))
        else:
            await client(JoinChannelRequest(s.lstrip("@")))
        return True
    except (InviteHashInvalidError, InviteHashExpiredError):
        return False
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        try:
            return await _ensure_join(client, s)
        except Exception:
            return False
    except Exception:
        return False


async def fetch_members_for_group_enhanced(client, group_identifier: str, filter_config: MemberFilter) -> list[dict]:
    """增强版群成员采集，支持过滤条件"""
    
    # 先尝试加入群组
    joined = await _ensure_join(client, group_identifier)
    
    try:
        entity = await client.get_entity(group_identifier)
        targets = [entity]
    except Exception:
        targets = []
        async for dialog in client.iter_dialogs():
            if isinstance(dialog.entity, (Channel, Chat)):
                targets.append(dialog.entity)

    results: list[dict] = []
    for t in targets:
        try:
            async for m in client.iter_participants(t):
                if isinstance(m, User) and _valid_user(m, filter_config):
                    # 只采集有用户名的用户，跳过没有用户名的用户
                    if getattr(m, "username", None):
                        ident = "@" + m.username
                        # 记录用户信息
                        user_info = {
                            "identifier": ident,
                            "is_bot": bool(getattr(m, "bot", False)),
                            "is_deleted": bool(getattr(m, "deleted", False)),
                            "has_username": True,
                            "has_phone": False,
                            "phone_raw": None,
                        }
                        
                        # 计算活跃度评分
                        activity_score = _calculate_activity_score(m)
                        
                        # 获取在线状态信息
                        status_info = "unknown"
                        if hasattr(m, 'status') and m.status:
                            if isinstance(m.status, UserStatusOnline):
                                status_info = "online"
                            elif isinstance(m.status, UserStatusRecently):
                                status_info = "recently"
                            elif isinstance(m.status, UserStatusLastWeek):
                                status_info = "last_week"
                            elif isinstance(m.status, UserStatusLastMonth):
                                status_info = "last_month"
                            elif isinstance(m.status, UserStatusOffline):
                                status_info = "offline"
                        
                        # 更新用户信息
                        user_info.update({
                            "activity_score": activity_score,
                            "status": status_info,
                            "last_seen": getattr(m.status, 'was_online', None) if hasattr(m, 'status') and m.status else None,
                            "has_photo": bool(getattr(m, "photo", None)),
                        })
                        
                        results.append(user_info)
                    # 没有用户名的用户直接跳过，不采集
        except Exception:
            continue
    
    # 去重
    seen = set()
    dedup: list[dict] = []
    for r in results:
        if r["identifier"] in seen:
            continue
        seen.add(r["identifier"])
        dedup.append(r)
    
    # 按活跃度排序
    dedup.sort(key=lambda x: x["activity_score"], reverse=True)
    
    return dedup


async def fetch_members_into_db_enhanced(
    repo: Repo, 
    account_index: int | None = None, 
    account_phone: str | None = None, 
    filter_config: MemberFilter = None,
    on_progress=None
) -> dict:
    """增强版群成员采集到数据库"""
    
    if filter_config is None:
        filter_config = MemberFilter()
    
    # 登录账号
    if account_phone:
        client, _ = await login_account_by_phone(account_phone)
    else:
        idx = account_index if account_index is not None else 0
        client, _ = await login_account_by_index(idx)

    totals = {"groups": 0, "members_added": 0, "targets_added": 0, "joined": 0, "filtered": 0}
    
    try:
        with repo.session() as s:
            groups = s.query(Group).filter(Group.fetched == False).all()  # noqa: E712
            group_items = [(g.id, g.link_or_username) for g in groups]

        for gid, ident in group_items:
            totals["groups"] += 1
            group_stats = {"members": 0, "targets": 0, "filtered": 0}
            
            # 加入群组
            try:
                joined = await _ensure_join(client, ident)
                if joined:
                    totals["joined"] += 1
                    # 更新加入状态
                    with repo.session() as s:
                        g = s.execute(select(Group).where(Group.id == gid)).scalar_one_or_none()
                        if g:
                            g.joined = True
                            s.commit()
            except Exception:
                pass
            
            # 采集成员
            members = await fetch_members_for_group_enhanced(client, ident, filter_config)
            if members:
                # 统计过滤信息
                total_members = len(members)
                filtered_members = total_members
                
                # 插入群成员表
                inserted = repo.add_group_members(gid, members)
                totals["members_added"] += inserted
                group_stats["members"] = inserted
                
                # 添加到发送目标表
                identifiers = [m["identifier"] for m in members]
                targets_inserted = repo.upsert_targets(identifiers, source=f"group_{gid}")
                totals["targets_added"] += targets_inserted
                group_stats["targets"] = targets_inserted
            
            # 无论是否采集到成员，都标记为已采集
            with repo.session() as s:
                g = s.execute(select(Group).where(Group.id == gid)).scalar_one_or_none()
                if g:
                    g.fetched = True
                    g.last_fetched_at = datetime.utcnow()
                    s.commit()
            
            # 报告进度
            if on_progress:
                on_progress(ident, group_stats)
    finally:
        await client.disconnect()
    
    return totals


# 便捷函数
async def fetch_online_members_only(repo: Repo, account_index: int = 0, on_progress=None) -> dict:
    """只采集当前在线的成员"""
    filter_config = MemberFilter(
        online_only=True,
        exclude_bots=True,
        exclude_deleted=True
    )
    return await fetch_members_into_db_enhanced(repo, account_index, None, filter_config, on_progress)


async def fetch_recent_members(repo: Repo, account_index: int = 0, recent_days: int = 7, on_progress=None) -> dict:
    """采集最近N天在线的成员"""
    filter_config = MemberFilter(
        recent_online_days=recent_days,
        exclude_bots=True,
        exclude_deleted=True,
        min_activity_score=10  # 最低活跃度
    )
    return await fetch_members_into_db_enhanced(repo, account_index, None, filter_config, on_progress)
