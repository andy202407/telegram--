from __future__ import annotations

import asyncio
from typing import Callable

from telethon.tl.functions.messages import SearchGlobalRequest
from telethon.tl.types import Channel, Chat, InputMessagesFilterEmpty, InputPeerEmpty
from telethon.tl.functions.contacts import GetContactsRequest

from ..db.repo import Repo
from ..login import login_account_by_index


async def search_groups_by_keywords(
    repo: Repo,
    keywords: list[str],
    account_index: int = 0,
    search_limit: int = 500,
    on_progress: Callable[[str, dict], None] | None = None,
) -> dict:
    """
    根据关键词搜索公开群组并保存到数据库
    
    Args:
        repo: 数据库仓库
        keywords: 搜索关键词列表
        account_index: 使用的账号索引
        search_limit: 每个关键词的搜索数量限制
        on_progress: 进度回调函数 (keyword, stats)
    
    Returns:
        统计信息 {"total_keywords": int, "groups_found": int, "groups_added": int}
    """
    on_progress = on_progress or (lambda k, s: None)
    
    client, _ = await login_account_by_index(account_index)
    
    total_found = 0
    total_added = 0
    all_groups = []
    
    try:
        for keyword in keywords:
            try:
                # 优化关键词：添加常见群组后缀
                search_keywords = [keyword]
                if keyword not in ['群', 'group', 'chat', 'discussion']:
                    search_keywords.extend([
                        f"{keyword}群",
                        f"{keyword}group", 
                        f"{keyword}chat",
                        f"{keyword}discussion",
                        f"{keyword}交流",
                        f"{keyword}讨论"
                    ])
                
                keyword_found = 0
                keyword_added = 0
                
                # 对每个搜索关键词进行搜索
                for search_keyword in search_keywords[:5]:  # 增加到5个变体
                    try:
                        # 使用全局搜索API搜索公开群组/频道
                        result = await client(SearchGlobalRequest(
                            q=search_keyword,
                            filter=InputMessagesFilterEmpty(),
                            min_date=None,
                            max_date=None,
                            offset_rate=0,
                            offset_peer=InputPeerEmpty(),
                            offset_id=0,
                            limit=min(search_limit // len(search_keywords), 200)  # 增加单次搜索限制
                        ))
                
                        # 遍历搜索结果，只保留公开群组
                        for chat in result.chats:
                            # 只处理频道（超级群组）
                            if isinstance(chat, Channel):
                                # 必须有用户名（公开群组才有用户名）
                                if not chat.username:
                                    continue
                                
                                # 调试信息：打印群组类型
                                is_megagroup = getattr(chat, 'megagroup', False)
                                is_broadcast = getattr(chat, 'broadcast', False)
                                
                                # 过滤条件：只要超级群组，不要广播频道
                                if is_broadcast:
                                    continue  # 跳过广播频道
                                
                                # 构建群组链接
                                link = f"https://t.me/{chat.username}"
                                
                                # 去重检查
                                if link not in [g['link'] for g in all_groups]:
                                    chat_type = '群组' if is_megagroup else '频道'
                                    all_groups.append({
                                        'link': link,
                                        'title': chat.title or '',
                                        'username': chat.username or '',
                                        'type': chat_type,
                                        'megagroup': is_megagroup,
                                        'broadcast': is_broadcast
                                    })
                                    keyword_found += 1
                            
                            # 普通群组（Chat类型）通常没有用户名，需要邀请链接才能加入
                            # 这里暂时跳过，因为无法通过公开链接加入
                    
                    except Exception as e:
                        # 某个搜索关键词失败，继续下一个
                        continue
                
                total_found += keyword_found
                
                # 保存到数据库
                if keyword_found > 0:
                    links = [g['link'] for g in all_groups[-keyword_found:]]
                    added = repo.upsert_groups(links)
                    keyword_added = added
                    total_added += added
                
                # 添加调试信息
                debug_info = []
                for group in all_groups[-keyword_found:]:
                    debug_info.append(f"{group['title']} ({group['type']}) - {group['link']}")
                
                on_progress(keyword, {
                    'found': keyword_found,
                    'added': keyword_added,
                    'debug': debug_info
                })
                
                # 避免请求过快，防止触发限流
                await asyncio.sleep(3)
                
            except Exception as e:
                on_progress(keyword, {
                    'found': 0,
                    'added': 0,
                    'error': str(e)
                })
    
    finally:
        await client.disconnect()
    
    return {
        'total_keywords': len(keywords),
        'groups_found': total_found,
        'groups_added': total_added
    }

