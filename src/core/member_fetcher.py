from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from telethon.errors import FloodWaitError, InviteHashInvalidError, InviteHashExpiredError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import Channel, Chat, User

from ..db.repo import Repo
from ..db.models import Group, GroupMember
from ..login import login_account_by_index, login_account_by_phone
from .phone_utils import format_phone_number, is_valid_phone_number


async def _ensure_join(client, link_or_username: str) -> None:
    s = (link_or_username or "").strip()
    if not s:
        return
    try:
        if s.startswith("https://t.me/+") or "/+" in s:
            invite_hash = s.split("/+", 1)[-1].split("?", 1)[0]
            await client(ImportChatInviteRequest(invite_hash))
        elif s.startswith("https://t.me/"):
            username = s.split("https://t.me/", 1)[-1].split("?", 1)[0]
            await client(JoinChannelRequest(username))
        else:
            await client(JoinChannelRequest(s.lstrip("@")))
    except (InviteHashInvalidError, InviteHashExpiredError):
        return
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        try:
            await _ensure_join(client, s)
        except Exception:
            return
    except Exception:
        return


def _valid_user(u: User) -> bool:
    if getattr(u, "deleted", False):
        return False
    if getattr(u, "bot", False):
        return False
    return True


async def fetch_members_for_group(client, group_identifier: str) -> list[dict]:
    await _ensure_join(client, group_identifier)

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
                if isinstance(m, User) and _valid_user(m):
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
                        results.append(user_info)
                    # 没有用户名的用户直接跳过，不采集
        except Exception:
            continue
    # de-duplicate
    seen = set()
    dedup: list[dict] = []
    for r in results:
        if r["identifier"] in seen:
            continue
        seen.add(r["identifier"])
        dedup.append(r)
    return dedup


async def fetch_members_into_db(repo: Repo, account_index: int | None = None, account_phone: str | None = None, on_progress=None) -> dict:
    # login
    if account_phone:
        client, _ = await login_account_by_phone(account_phone)
    else:
        idx = account_index if account_index is not None else 0
        client, _ = await login_account_by_index(idx)

    totals = {"groups": 0, "members_added": 0, "targets_added": 0, "joined": 0}
    try:
        with repo.session() as s:
            groups = s.query(Group).filter(Group.fetched == False).all()  # noqa: E712
            group_items = [(g.id, g.link_or_username) for g in groups]

        for gid, ident in group_items:
            totals["groups"] += 1
            group_stats = {"members": 0, "targets": 0}
            
            # Join group first
            try:
                await _ensure_join(client, ident)
                totals["joined"] += 1
                # Update joined status
                with repo.session() as s:
                    g = s.execute(select(Group).where(Group.id == gid)).scalar_one_or_none()
                    if g:
                        g.joined = True
                        s.commit()
            except Exception:
                pass
            
            # Fetch members
            members = await fetch_members_for_group(client, ident)
            if members:
                # Insert into group_members
                inserted = repo.add_group_members(gid, members)
                totals["members_added"] += inserted
                group_stats["members"] = inserted
                
                # Also add to targets table for sending
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
            
            # Report progress for this group
            if on_progress:
                on_progress(ident, group_stats)
    finally:
        await client.disconnect()
    return totals


