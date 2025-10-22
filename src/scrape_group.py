import argparse
import asyncio
from pathlib import Path
from typing import Iterable

from telethon.errors import FloodWaitError, InviteHashInvalidError, InviteHashExpiredError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import Channel, Chat, User

from .login import login_account_by_index, login_account_by_phone
from .utils import get_groups_file, get_resource_path
from .core.phone_utils import format_phone_number, is_valid_phone_number


DEFAULT_GROUPS_FILE = get_groups_file()
DEFAULT_OUTPUT_FILE = get_resource_path("群/members.txt")


def read_groups(file_path: Path) -> list[str]:
    if not file_path.exists():
        raise FileNotFoundError(f"Groups file not found: {file_path}")
    groups: list[str] = []
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            url = line.strip()
            if not url:
                continue
            groups.append(url)
    if not groups:
        raise ValueError("Groups file is empty")
    return groups


async def ensure_join_group(client, link_or_username: str) -> None:
    s = link_or_username.strip()
    if not s:
        return
    try:
        if s.startswith("https://t.me/+") or "/+" in s:
            # Private invite link
            invite_hash = s.split("/+", 1)[-1]
            invite_hash = invite_hash.split("?", 1)[0]
            await client(ImportChatInviteRequest(invite_hash))
        elif s.startswith("https://t.me/"):
            username = s.split("https://t.me/", 1)[-1].split("?", 1)[0]
            await client(JoinChannelRequest(username))
        else:
            # Assume raw username without @
            username = s.lstrip("@")
            await client(JoinChannelRequest(username))
    except (InviteHashInvalidError, InviteHashExpiredError):
        # Ignore invalid/expired invites; continue with others
        pass
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        try:
            await ensure_join_group(client, s)
        except Exception:
            pass
    except Exception:
        # best effort: ignore
        pass


def is_valid_user(u: User) -> bool:
    # Filter out deleted users
    if getattr(u, "deleted", False):
        return False
    # Filter out bots
    if getattr(u, "bot", False):
        return False
    return True


async def collect_members(client, dialogs: list[str]) -> list[str]:
    # Join groups then collect members
    for g in dialogs:
        await ensure_join_group(client, g)

    # After joining, iterate dialogs to find corresponding entities
    usernames_or_phones: list[str] = []
    for g in dialogs:
        try:
            entity = await client.get_entity(g)
        except Exception:
            # if raw invite link, we can't get by link; skip resolve and rely on joined list
            entity = None

        targets = []
        if entity is not None:
            targets = [entity]
        else:
            # fallback: iterate over dialogs to find channels/chats
            async for dialog in client.iter_dialogs():
                if isinstance(dialog.entity, (Channel, Chat)):
                    targets.append(dialog.entity)

        for t in targets:
            try:
                async for member in client.iter_participants(t):
                    if isinstance(member, User) and is_valid_user(member):
                        # 只采集有用户名的用户，跳过没有用户名的用户
                        if getattr(member, "username", None):
                            usernames_or_phones.append("@" + member.username)
                        # 没有用户名的用户直接跳过，不采集
            except Exception:
                continue

    # de-duplicate while preserving order
    seen = set()
    result: list[str] = []
    for x in usernames_or_phones:
        if x not in seen:
            seen.add(x)
            result.append(x)
    return result


async def run(account_index: int | None, phone: str | None, groups_file: Path, output_file: Path) -> None:
    if phone:
        client, _ = await login_account_by_phone(phone)
    else:
        idx = account_index if account_index is not None else 0
        client, _ = await login_account_by_index(idx)

    try:
        groups = read_groups(groups_file)
        members = await collect_members(client, groups)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open("w", encoding="utf-8") as f:
            for item in members:
                f.write(item + "\n")
        print({"groups": len(groups), "exported": len(members), "file": str(output_file)})
    finally:
        await client.disconnect()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Join groups and export members")
    parser.add_argument("--index", type=int, default=None, help="Account index (0-based)")
    parser.add_argument("--phone", type=str, default=None, help="Use account by phone")
    parser.add_argument("--groups", type=str, default=str(DEFAULT_GROUPS_FILE), help="Groups file path")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUTPUT_FILE), help="Output file path")
    return parser


async def _main_async() -> None:
    args = build_parser().parse_args()
    await run(
        account_index=args.index,
        phone=args.phone,
        groups_file=Path(args.groups),
        output_file=Path(args.out),
    )


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()


