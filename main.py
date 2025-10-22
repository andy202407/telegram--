import argparse
import asyncio
from pathlib import Path

from src.broadcast import send_messages, DEFAULT_RECIPIENTS_FILE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram 登录与群发入口")
    parser.add_argument(
        "--index",
        type=int,
        default=None,
        help="账号序号(0起)，若未提供 --phone 则默认 0",
    )
    parser.add_argument(
        "--phone",
        type=str,
        default=None,
        help="按手机号选择账号(优先于 --index)",
    )
    parser.add_argument(
        "--message",
        type=str,
        default="这是测试消息",
        help="要发送的消息内容（默认：这是测试消息）",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=str(DEFAULT_RECIPIENTS_FILE),
        help="收件人列表文件路径，默认 群发目标/user.txt",
    )
    return parser


async def _main_async() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # 直接调用群发（内部会处理登录）
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


