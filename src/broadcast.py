import argparse
import asyncio
from pathlib import Path
from typing import Iterable

from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, UsernameInvalidError
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputPhoneContact
from telethon.tl.types import MessageEntityBold

from .login import (
    login_account_by_index,
    login_account_by_phone,
)
from .utils import get_targets_file


DEFAULT_RECIPIENTS_FILE = get_targets_file()


def read_recipients(file_path: Path) -> list[str]:
    if not file_path.exists():
        raise FileNotFoundError(f"Recipients file not found: {file_path}")
    recipients: list[str] = []
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            user = line.strip()
            if not user:
                continue
            recipients.append(user)
    if not recipients:
        raise ValueError("Recipients file is empty")
    return recipients


async def send_messages(
    account_index: int | None,
    phone: str | None,
    message: str,
    recipients_file: Path = DEFAULT_RECIPIENTS_FILE,
    image_path: str | None = None,
) -> None:
    if not message:
        raise ValueError("Message cannot be empty")

    recipients = read_recipients(recipients_file)

    # Login with selected account
    if phone:
        client, _ = await login_account_by_phone(phone)
    else:
        index = account_index if account_index is not None else 0
        client, _ = await login_account_by_index(index)

    try:
        # Split recipients into phones and direct identifiers
        phone_list: list[str] = []
        direct_list: list[str] = []
        for t in recipients:
            s = t.strip()
            if not s:
                continue
            if s.startswith("+") or s.isdigit():
                phone_list.append(s)
            else:
                direct_list.append(s)

        # Resolve phones to user entities by importing as contacts
        phone_contacts: list[InputPhoneContact] = []
        for i, p in enumerate(phone_list):
            phone_num = p.lstrip("+")
            if not phone_num:
                continue
            phone_contacts.append(InputPhoneContact(client_id=i, phone=phone_num, first_name=".", last_name=""))

        phone_map: dict[str, int] = {}
        if phone_contacts:
            try:
                result = await client(ImportContactsRequest(phone_contacts))
                for user in result.users:
                    if getattr(user, "phone", None):
                        phone_map[str(user.phone)] = user.id
            except Exception:
                # If import fails, we fallback to trying raw phone strings later which may fail on privacy
                phone_map = {}

        success = 0
        failed = 0

        # Send to direct recipients (username or user id)
        for target in direct_list:
            try:
                if image_path:
                    await client.send_file(entity=target, file=image_path, caption=message)
                else:
                    await client.send_message(entity=target, message=message)
                success += 1
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
                try:
                    if image_path:
                        await client.send_file(entity=target, file=image_path, caption=message)
                    else:
                        await client.send_message(entity=target, message=message)
                    success += 1
                except Exception as ex:
                    print(f"❌ FloodWait重试失败: {target} - {ex}")
                    failed += 1
            except (UserPrivacyRestrictedError, UsernameInvalidError) as e:
                print(f"❌ 隐私限制/用户名无效: {target} - {e}")
                failed += 1
            except Exception as e:
                print(f"❌ 发送失败: {target} - {e}")
                failed += 1
            await asyncio.sleep(0.5)

        # Send to phone recipients
        for original in phone_list:
            key = original.lstrip("+")
            target_entity = phone_map.get(key, original)
            try:
                if image_path:
                    await client.send_file(entity=target_entity, file=image_path, caption=message)
                else:
                    await client.send_message(entity=target_entity, message=message)
                success += 1
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
                try:
                    if image_path:
                        await client.send_file(entity=target_entity, file=image_path, caption=message)
                    else:
                        await client.send_message(entity=target_entity, message=message)
                    success += 1
                except Exception as ex:
                    print(f"❌ FloodWait重试失败: {original} - {ex}")
                    failed += 1
            except (UserPrivacyRestrictedError, UsernameInvalidError) as e:
                print(f"❌ 隐私限制/用户名无效: {original} - {e}")
                failed += 1
            except Exception as e:
                print(f"❌ 发送失败: {original} - {e}")
                failed += 1
            await asyncio.sleep(0.5)

        print({"sent": success, "failed": failed, "total": len(recipients)})
    finally:
        await client.disconnect()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram broadcast sender")
    parser.add_argument(
        "--index",
        type=int,
        default=None,
        help="Account index (0-based). Default 0 if --phone not provided.",
    )
    parser.add_argument(
        "--phone",
        type=str,
        default=None,
        help="Use account by phone number (overrides --index)",
    )
    parser.add_argument(
        "--message",
        type=str,
        required=True,
        help="Message text to send",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=str(DEFAULT_RECIPIENTS_FILE),
        help="Recipients file path (default: 群发目标/user.txt)",
    )
    return parser


async def _main_async() -> None:
    parser = build_parser()
    args = parser.parse_args()
    await send_messages(
        account_index=args.index,
        phone=args.phone,
        message=args.message,
        recipients_file=Path(args.file),
    )


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()


