import asyncio
import json
from pathlib import Path
from typing import Dict, Optional, Tuple

from telethon import TelegramClient
from .utils import get_accounts_dir


DEFAULT_ACCOUNTS_DIR = get_accounts_dir()


def _find_first_account_json(accounts_dir: Path) -> Path:
    candidate_files = sorted(accounts_dir.glob("*.json"))
    if not candidate_files:
        raise FileNotFoundError(f"No .json account files found in {accounts_dir}")
    return candidate_files[0]


def _list_account_jsons(accounts_dir: Path) -> list[Path]:
    files = sorted(accounts_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No .json account files found in {accounts_dir}")
    return files


def _load_account_config(json_path: Path) -> Dict:
    with json_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_session_path(accounts_dir: Path, session_file_stem: str) -> Path:
    # Telethon accepts a stem; it will append .session. We ensure files live under accounts_dir.
    # If an existing .session file exists, Telethon will reuse it.
    session_stem_path = accounts_dir / session_file_stem
    return session_stem_path


def create_client_from_account(accounts_dir: Path, account_config: Dict) -> TelegramClient:
    # 支持多种API字段格式
    api_id = None
    api_hash = None
    
    if "app_id" in account_config:
        api_id = int(account_config["app_id"])
    elif "api_id" in account_config:
        api_id = int(account_config["api_id"])
    
    if "app_hash" in account_config:
        api_hash = str(account_config["app_hash"])
    elif "api_hash" in account_config:
        api_hash = str(account_config["api_hash"])
    
    if not api_id or not api_hash:
        raise ValueError("Missing 'app_id'/'api_id' or 'app_hash'/'api_hash' in account config")
    
    session_stem = str(account_config.get("session_file") or account_config.get("phone"))
    if not session_stem:
        raise ValueError("Missing 'session_file' (or fallback 'phone') in account config")

    # 处理session文件路径
    if "/" in session_stem or "\\" in session_stem:
        # 如果是绝对路径，直接使用
        session_path = Path(session_stem)
        # 如果绝对路径的session文件不存在，尝试在协议号文件夹中查找
        if not session_path.exists():
            print(f"⚠️ JSON中指定的session文件不存在: {session_path}")
            # 提取文件名
            session_filename = session_path.name
            fallback_path = accounts_dir / session_filename
            if fallback_path.exists():
                print(f"✅ 在协议号文件夹中找到session文件: {fallback_path}")
                session_path = fallback_path
            else:
                print(f"❌ 协议号文件夹中也没有找到session文件: {fallback_path}")
                raise FileNotFoundError(f"Session file not found: {session_stem}")
    else:
        # 如果是相对路径，使用accounts_dir
        session_path = _resolve_session_path(accounts_dir, session_stem)
    
    print(f"🔗 使用session文件: {session_path}")
    client = TelegramClient(str(session_path), api_id, api_hash)
    return client


async def login_first_account(accounts_dir: Optional[Path] = None) -> Tuple[TelegramClient, Dict]:
    """
    Create and connect a Telethon client using the first account json found.

    Returns (client, account_config). The client is connected and authorized.
    """
    accounts_dir = Path(accounts_dir) if accounts_dir else DEFAULT_ACCOUNTS_DIR
    json_path = _find_first_account_json(accounts_dir)
    account_config = _load_account_config(json_path)
    client = create_client_from_account(accounts_dir, account_config)

    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError(
            "Session not authorized. Ensure the .session file is valid for this account."
        )

    return client, account_config


def login_first_account_sync(accounts_dir: Optional[Path] = None) -> Tuple[TelegramClient, Dict]:
    """Synchronous helper for environments not using asyncio explicitly."""
    return asyncio.get_event_loop().run_until_complete(login_first_account(accounts_dir))


async def login_account_by_index(index: int, accounts_dir: Optional[Path] = None) -> Tuple[TelegramClient, Dict]:
    """
    Login using the nth account (0-based). For "第二个账号" use index=1.
    """
    accounts_dir = Path(accounts_dir) if accounts_dir else DEFAULT_ACCOUNTS_DIR
    files = _list_account_jsons(accounts_dir)
    if index < 0 or index >= len(files):
        raise IndexError(f"index {index} out of range, total accounts: {len(files)}")
    account_config = _load_account_config(files[index])
    client = create_client_from_account(accounts_dir, account_config)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Session not authorized for selected account")
    return client, account_config


async def login_account_by_phone(phone: str, accounts_dir: Optional[Path] = None) -> Tuple[TelegramClient, Dict]:
    """
    Login using the account whose json has matching "phone" or "session_file".
    """
    accounts_dir = Path(accounts_dir) if accounts_dir else DEFAULT_ACCOUNTS_DIR
    for json_path in _list_account_jsons(accounts_dir):
        cfg = _load_account_config(json_path)
        # 支持多种phone字段格式
        cfg_phone = None
        if 'phone' in cfg:
            cfg_phone = str(cfg.get("phone"))
        elif 'phone_number' in cfg:
            cfg_phone = str(cfg.get("phone_number"))
        
        # 清理电话号码格式进行匹配
        if cfg_phone:
            cfg_phone = cfg_phone.replace(" ", "").replace("+", "").replace("-", "")
        
        # 检查session文件名是否匹配
        session_file = cfg.get("session_file")
        session_file_name = None
        if session_file:
            if "/" in session_file or "\\" in session_file:
                # 绝对路径，提取文件名
                session_file_name = Path(session_file).stem
            else:
                # 相对路径，直接使用
                session_file_name = Path(session_file).stem
        
        if cfg_phone == str(phone) or session_file_name == str(phone):
            client = create_client_from_account(accounts_dir, cfg)
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("Session not authorized for selected phone")
            return client, cfg
    raise FileNotFoundError(f"No account json matched phone: {phone}")


async def _main() -> None:
    # Example: change to index=1 for the second account
    client, info = await login_account_by_index(1)
    me = await client.get_me()
    print({
        "user_id": me.id if me else None,
        "phone": info.get("phone"),
        "first_name": getattr(me, "first_name", None) if me else None,
        "username": getattr(me, "username", None) if me else None,
    })
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(_main())


