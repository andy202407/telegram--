from __future__ import annotations

import asyncio
import re
from typing import Callable, Iterable

from telethon.tl.types import Message, KeyboardButtonCallback, KeyboardButtonUrl
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest

from ..db.repo import Repo
from ..login import login_account_by_index


# 更宽松的链接正则：允许 t.me/ 后面出现 / 和更多字符，直到空白或引号
_LINK_RE = re.compile(r"https?://t\.me/[^\s'\"<>]+", re.IGNORECASE)


def _extract_links(text: str) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(m.group(0) for m in _LINK_RE.finditer(text)))


def _extract_links_from_buttons(msg: Message) -> list[str]:
    """从消息按钮中提取链接（兼容多种 button 类型）"""
    links = []
    if not getattr(msg, "buttons", None):
        return links
    
    for row in msg.buttons:
        for btn in row:
            # Telethon 的按钮可能是不同类型，优先取 .url
            url = getattr(btn, "url", None)
            if url and "t.me/" in url:
                links.append(url)
            # 有些按钮可能在 .text 里包含可点的 url（不常见）
            txt = getattr(btn, "text", "") or ""
            for l in _LINK_RE.findall(txt):
                links.append(l)
    return list(dict.fromkeys(links))


def _extract_links_from_entities(msg: Message) -> list[str]:
    """从 message.entities（如果存在）提取 URL（比如 MessageEntityTextUrl / MessageEntityUrl）"""
    links = []
    if getattr(msg, 'entities', None):
        for ent in msg.entities:
            # ent.url for MessageEntityTextUrl
            url = getattr(ent, 'url', None)
            if url and 't.me/' in url:
                links.append(url)
            # 如果是 MessageEntityUrl (no .url), extract from text slice
            try:
                from telethon.tl.types import MessageEntityUrl
                if isinstance(ent, MessageEntityUrl):
                    txt = msg.message or ''
                    url_candidate = txt[ent.offset: ent.offset + ent.length]
                    if 't.me/' in url_candidate:
                        links.append(url_candidate)
            except Exception:
                pass
    return list(dict.fromkeys(links))


async def _get_latest_bot_message(client, bot_peer, max_scan=10) -> Message | None:
    """
    获取与 bot 的最近一条来自机器人的消息（跳过自己发出的消息）。
    扫描最近 max_scan 条消息以避免拿到自己的消息。
    """
    try:
        # 从最新的若干条消息里找第一个 m.out == False（来自对方/机器人）消息
        async for m in client.iter_messages(bot_peer, limit=max_scan):
            # Telethon 的 Message 有 .out 属性（True 表示由当前用户发送）
            if getattr(m, 'out', False):
                # 是自己发的，跳过
                continue
            return m
    except Exception as e:
        print(f"获取消息失败: {e}")
        pass
    return None


def _find_next_button(msg: Message) -> KeyboardButtonCallback | None:
    if not getattr(msg, "buttons", None):
        return None
    labels = [
        "下一页", "下页", "更多", "Next", "More", "下一頁", "▶", "➡", "下一", "下一页👉",
    ]
    for row in (msg.buttons or []):
        for btn in row:
            if isinstance(btn, KeyboardButtonUrl):
                continue
            label = getattr(btn, "text", "") or ""
            if any(x in label for x in labels):
                return btn  # type: ignore[return-value]
    return None


def _find_search_button(msg: Message) -> KeyboardButtonCallback | None:
    """查找搜索相关的按钮"""
    if not getattr(msg, "buttons", None):
        return None
    search_labels = ["搜索", "Search", "🔍", "查找", "Find"]
    for row in (msg.buttons or []):
        for btn in row:
            if isinstance(btn, KeyboardButtonUrl):
                continue
            label = getattr(btn, "text", "") or ""
            if any(x in label for x in search_labels):
                return btn  # type: ignore[return-value]
    return None


def _normalize_unicode_digits_to_ascii(text: str) -> str:
    """
    将文本中能转换为十进制数字的 Unicode 字符转换为对应的 ASCII 数字，
    特殊运算符转换为标准符号，其余字符保持原样。
    使用 unicodedata.digit / numeric 做尽可能的兼容。
    """
    import unicodedata
    # 特殊运算符映射
    operator_map = {
        '➕': '+', '✚': '+', '✙': '+', '＋': '+',
        '➖': '-', '✖': '*', '➗': '/', '＝': '='
    }
    
    out_chars = []
    for ch in text:
        # 检查是否是特殊运算符
        if ch in operator_map:
            out_chars.append(operator_map[ch])
            continue
        # 尝试 decimal digit （比如全角数字、其他十进制数字）
        try:
            d = unicodedata.digit(ch)
            out_chars.append(str(d))
            continue
        except (TypeError, ValueError):
            pass
        # 尝试 numeric（有些圈号、特殊数字可能是 numeric）
        try:
            n = unicodedata.numeric(ch)
            # 只在它是整数时保留（比如 ➉ -> 10）
            if int(n) == n:
                out_chars.append(str(int(n)))
                continue
        except (TypeError, ValueError):
            pass
        # 其他字符原样保留
        out_chars.append(ch)
    return ''.join(out_chars)


async def search_groups_via_bot(
    repo: Repo,
    keywords: list[str],
    account_index: int = 0,
    bot_username: str = "@soso",
    max_pages_per_keyword: int = 30,
    per_page_delay_sec: float = 1.2,
    on_progress: Callable[[str, dict], None] | None = None,
) -> dict:
    """Use a search bot to collect public group links and save to DB.

    It sends keywords to a bot (default @soso), iterates result pages by
    clicking inline callback buttons, extracts t.me links, filters public
    groups (username links), and upserts them via the repository.
    """
    on_progress = on_progress or (lambda k, s: None)

    client, _ = await login_account_by_index(account_index)

    total_found = 0
    total_added = 0

    try:
        bot = await client.get_entity(bot_username)

        for kw in keywords:
            links_accum: list[str] = []

            # Start conversation: send keyword
            await client.send_message(bot, kw)
            # Wait for bot response
            await asyncio.sleep(3)
            
            # Check if bot asks for verification with retry mechanism
            verification_success = False
            max_retries = 10
            
            for retry_count in range(max_retries):
                first_msg = await _get_latest_bot_message(client, bot)
                if first_msg:
                    msg_text = getattr(first_msg, "message", "") or ""
                    
                    # 检查是否是验证码消息
                    if "请点击正确答案" in msg_text or "=" in msg_text and "?" in msg_text:
                        # 规范化 Unicode 数字到 ASCII，方便后续匹配
                        normalized = _normalize_unicode_digits_to_ascii(msg_text)
                        # debug
                        on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"验证码原文(重试{retry_count+1}/{max_retries}): {msg_text[:80]}", f"规范化: {normalized[:120]}"]})

                        # 尝试在规范化文本中匹配两个操作数（支持 + 或 特殊加号，或者只有空格）
                        import re
                        # 先尝试匹配有运算符的情况
                        math_pattern = re.compile(r"(\d+)\s*[+\-*/✚✙加]\s*(\d+)")
                        match = math_pattern.search(normalized)
                        # 如果没匹配到，尝试只有空格的情况（默认为加法）
                        if not match:
                            math_pattern = re.compile(r"(\d+)\s+(\d+)\s*=")
                            match = math_pattern.search(normalized)
                        # 如果还没匹配到，尝试更宽松的空格匹配
                        if not match:
                            math_pattern = re.compile(r"(\d+)\s{1,}\s*(\d+)\s*=")
                            match = math_pattern.search(normalized)
                        answer = None
                        if match:
                            a = int(match.group(1))
                            b = int(match.group(2))
                            answer = a + b  # 注意：这里假设是加法，若可能有别的运算符需要扩展
                            on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"解析到算式: {a} + {b} = {answer}"]})

                        if answer is not None:
                            # 先尝试在按钮中找到对应答案并点击（优先）
                            clicked = False
                            if getattr(first_msg, "buttons", None):
                                # 扁平化按钮并尝试匹配文本为答案（也尝试包含答案的情形）
                                for row in (first_msg.buttons or []):
                                    for btn in row:
                                        # 跳过 url 按钮
                                        if isinstance(btn, KeyboardButtonUrl):
                                            continue
                                        text = getattr(btn, "text", "") or ""
                                        # 规范化按钮文本里的数字（以防按钮上也用了全角/圈号）
                                        norm_btn_text = _normalize_unicode_digits_to_ascii(text).strip()
                                        if str(answer) == norm_btn_text or str(answer) in norm_btn_text:
                                            try:
                                                # 使用 Telethon 的 click 方法更可靠
                                                await first_msg.click(data=btn.data)
                                                clicked = True
                                                on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"点击了按钮: {text} (匹配答案 {answer})"]})
                                                await asyncio.sleep(1.5)
                                                verification_success = True
                                                break
                                            except Exception as e:
                                                on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"点击按钮失败: {e}"]})
                                    if clicked:
                                        break

                            if not clicked:
                                # 回退策略：以文本发送答案（有些机器人也接受文本答案）
                                try:
                                    await client.send_message(bot, str(answer))
                                    on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"未找到匹配按钮，发送文本答案: {answer}"]})
                                    await asyncio.sleep(1.5)
                                    verification_success = True
                                except Exception as e:
                                    on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"发送文本答案失败: {e}"]})

                            # 点击/发送答案后，等待并再次发送搜索关键词
                            await asyncio.sleep(1.5)
                            await client.send_message(bot, kw)
                            await asyncio.sleep(2.5)
                            break  # 验证成功，跳出重试循环
                        else:
                            on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"未能解析算式: {normalized[:80]}, 重试 {retry_count+1}/{max_retries}"]})
                            if retry_count < max_retries - 1:
                                await asyncio.sleep(2)  # 等待后重试
                                continue
                    else:
                        # 不是验证码消息，可能是搜索结果
                        on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"收到搜索结果: {msg_text[:80]}"]})
                        verification_success = True
                        break  # 不是验证码，跳出重试循环
                else:
                    # 没有收到消息，重试
                    on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"未收到消息, 重试 {retry_count+1}/{max_retries}"]})
                    if retry_count < max_retries - 1:
                        await asyncio.sleep(2)
                        continue
            
            if not verification_success:
                on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"验证失败，已重试 {max_retries} 次"]})
                continue  # 跳过这个关键词

            # Iterate pages
            pages = 0
            last_msg = await _get_latest_bot_message(client, bot)
            if not last_msg:
                on_progress(kw, {"found": 0, "added": 0, "pages": 0, "error": "无法获取机器人消息"})
                continue
            
            # Debug: 显示机器人消息内容
            msg_text = getattr(last_msg, "message", "") or ""
            msg_len = len(msg_text)
            has_buttons = bool(getattr(last_msg, "buttons", None))
            # 移除表情符号显示消息前200字符用于调试
            import re
            preview = re.sub(r'[^\w\s\u4e00-\u9fff.,!?()[]{}@#$%^&*+=|\\:";\'<>?/~`]', '', msg_text[:200])
            
            # 检查是否有搜索按钮
            search_btn = _find_search_button(last_msg)
            if search_btn:
                on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"找到搜索按钮: {getattr(search_btn, 'text', '')}"]})
                # 点击搜索按钮
                try:
                    await client(GetBotCallbackAnswerRequest(bot, last_msg.id, data=search_btn.data))
                    await asyncio.sleep(2)
                    # 重新获取消息
                    last_msg = await _get_latest_bot_message(client, bot)
                except Exception as e:
                    on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"点击搜索按钮失败: {e}"]})
            
            # 如果消息内容就是关键词，说明需要进一步操作
            if msg_text.strip() == kw.strip():
                on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"机器人只返回了关键词，需要进一步操作"]})
                # 尝试发送 /start 命令
                try:
                    await client.send_message(bot, "/start")
                    await asyncio.sleep(2)
                    last_msg = await _get_latest_bot_message(client, bot)
                    if last_msg:
                        msg_text = getattr(last_msg, "message", "") or ""
                        preview = re.sub(r'[^\w\s\u4e00-\u9fff.,!?()[]{}@#$%^&*+=|\\:";\'<>?/~`]', '', msg_text[:200])
                        on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"发送 /start 后: {preview}"]})
                except Exception as e:
                    on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [f"发送 /start 失败: {e}"]})
            
            # 详细调试输出
            msg_out = getattr(last_msg, 'out', None)
            msg_from = getattr(last_msg, 'from_id', None)
            msg_entities = getattr(last_msg, 'entities', None)
            on_progress(kw, {"found": 0, "added": 0, "pages": 0, "debug": [
                f"消息预览: {preview.replace('👇', '').replace('👆', '')}",
                f"消息长度: {msg_len}",
                f"有按钮: {has_buttons}",
                f"是自己发的: {msg_out}",
                f"来自: {msg_from}",
                f"有entities: {msg_entities is not None}"
            ]})
                
            while pages < max_pages_per_keyword and last_msg is not None:
                # Extract links from current page message
                msg_text = getattr(last_msg, "message", "") or ""
                text_links = _extract_links(msg_text)
                button_links = _extract_links_from_buttons(last_msg)
                entity_links = _extract_links_from_entities(last_msg)
                all_links = text_links + button_links + entity_links
                
                # Keep public groups only (username, not +invite, not channels)
                page_links = []
                filtered_info = []
                
                for l in all_links:
                    username = l.split("t.me/", 1)[-1]
                    
                    # 过滤掉邀请链接
                    if username.startswith("+"):
                        filtered_info.append(f"❌ {l} (邀请链接)")
                        continue
                    
                    # 尝试获取群组信息来验证是否为群组
                    try:
                        if username:
                            # 获取群组实体
                            entity = await client.get_entity(username)
                            # 检查是否为群组（不是频道）
                            is_group = False
                            if hasattr(entity, 'megagroup') and entity.megagroup:
                                # 这是超级群组
                                is_group = True
                            elif hasattr(entity, 'broadcast') and not entity.broadcast:
                                # 这是群组，不是广播频道
                                is_group = True
                            
                            if is_group:
                                # 检查群组成员数量
                                try:
                                    # 先尝试获取成员数
                                    full_info = await client.get_full_channel(entity)
                                    member_count = full_info.full_chat.participants_count
                                    
                                    if member_count >= 20:
                                        page_links.append(l)
                                        filtered_info.append(f"✅ {l} (群组, {member_count}人)")
                                    else:
                                        filtered_info.append(f"❌ {l} (群组, 仅{member_count}人, <20人)")
                                except Exception:
                                    # 如果无法获取成员数，尝试加入群组后再获取
                                    try:
                                        # 参考 member_fetcher.py 的 _ensure_join 逻辑
                                        if l.startswith("https://t.me/+") or "/+" in l:
                                            invite_hash = l.split("/+", 1)[-1].split("?", 1)[0]
                                            from telethon.tl.functions.messages import ImportChatInviteRequest
                                            await client(ImportChatInviteRequest(invite_hash))
                                        else:
                                            from telethon.tl.functions.channels import JoinChannelRequest
                                            await client(JoinChannelRequest(username))
                                        
                                        await asyncio.sleep(1)
                                        
                                        # 重新尝试获取成员数
                                        full_info = await client.get_full_channel(entity)
                                        member_count = full_info.full_chat.participants_count
                                        
                                        if member_count >= 20:
                                            page_links.append(l)
                                            filtered_info.append(f"✅ {l} (群组, {member_count}人, 已加入)")
                                        else:
                                            filtered_info.append(f"❌ {l} (群组, 仅{member_count}人, <20人)")
                                    except Exception:
                                        # 尝试统计参与者数量
                                        try:
                                            participant_count = 0
                                            async for participant in client.iter_participants(entity, limit=100):
                                                participant_count += 1
                                            
                                            if participant_count >= 20:
                                                page_links.append(l)
                                                filtered_info.append(f"✅ {l} (群组, 约{participant_count}+人)")
                                            else:
                                                filtered_info.append(f"❌ {l} (群组, 约{participant_count}人, <20人)")
                                        except Exception:
                                            # 无法获取任何信息，但确认是群组
                                            page_links.append(l)
                                            filtered_info.append(f"⚠️ {l} (群组, 无法获取成员数)")
                            else:
                                filtered_info.append(f"❌ {l} (频道)")
                    except Exception as e:
                        # 如果无法获取群组信息，使用基础过滤
                        username_lower = username.lower()
                        # 过滤掉明显的频道关键词
                        channel_keywords = ['channel', 'ch', 'news', 'official', 'update', 'announcement', 'broadcast']
                        if any(keyword in username_lower for keyword in channel_keywords):
                            filtered_info.append(f"❌ {l} (疑似频道)")
                        elif username_lower.isdigit() or username_lower.startswith('c/'):
                            filtered_info.append(f"❌ {l} (数字ID/频道)")
                        else:
                            # 无法验证，但保留
                            page_links.append(l)
                            filtered_info.append(f"⚠️ {l} (无法验证，保留)")
                
                # Debug: 显示提取的链接
                debug_info = [
                    f"文本链接: {len(text_links)} 个", 
                    f"按钮链接: {len(button_links)} 个",
                    f"entity链接: {len(entity_links)} 个",
                    f"过滤后(≥20人): {len(page_links)} 个"
                ]
                
                # 显示所有链接的过滤结果
                debug_info.append("链接过滤详情:")
                for info in filtered_info[:10]:  # 显示前10个
                    debug_info.append(f"  {info}")
                if len(filtered_info) > 10:
                    debug_info.append(f"  ... 还有 {len(filtered_info) - 10} 个")
                
                on_progress(kw, {"found": len(links_accum), "added": 0, "pages": pages, "debug": debug_info})
                
                for l in page_links:
                    if l not in links_accum:
                        links_accum.append(l)

                # Click next button if present
                btn = _find_next_button(last_msg)
                if not btn:
                    on_progress(kw, {"found": len(links_accum), "added": 0, "pages": pages, "debug": ["未找到下一页按钮，停止翻页"]})
                    break
                try:
                    # 使用 msg.click() 方法点击按钮
                    await last_msg.click(data=btn.data)
                    on_progress(kw, {"found": len(links_accum), "added": 0, "pages": pages, "debug": [f"点击下一页按钮: {getattr(btn, 'text', '')}"]})
                except Exception as e:
                    on_progress(kw, {"found": len(links_accum), "added": 0, "pages": pages, "debug": [f"点击下一页按钮失败: {e}"]})
                    break

                await asyncio.sleep(per_page_delay_sec)
                pages += 1
                last_msg = await _get_latest_bot_message(client, bot)

            # Save to DB
            added = 0
            if links_accum:
                added = repo.upsert_groups(links_accum)
            total_found += len(links_accum)
            total_added += added

            on_progress(kw, {
                "found": len(links_accum),
                "added": added,
                "pages": pages,
                "debug": links_accum[:5] if links_accum else []  # 显示前5个链接用于调试
            })

            await asyncio.sleep(max(per_page_delay_sec, 1.0))

    finally:
        await client.disconnect()

    return {"groups_found": total_found, "groups_added": total_added}


