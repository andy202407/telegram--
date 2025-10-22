from __future__ import annotations

import asyncio
import random
from datetime import datetime
import pytz
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, UsernameInvalidError
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputPhoneContact

from ..db.repo import Repo
from ..db.models import Account, Target, SendRun, SendLog
from ..login import login_account_by_phone


class SenderEngine:
    def __init__(
        self,
        repo: Repo,
        settings: dict[str, Any],
        on_progress: Callable[[dict], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ):
        self.repo = repo
        self.settings = settings
        self.on_progress = on_progress or (lambda x: None)
        self.on_log = on_log or (lambda x: None)
        self.stopped = False
        self._tasks = []  # 存储所有运行中的任务

    async def send_bulk(self, message: str, image_path: str | None = None) -> dict:
        """Execute bulk send with configured settings."""
        print("DEBUG: send_bulk开始执行")
        
        # Create run record
        print("DEBUG: 创建运行记录")
        with self.repo.session() as s:
            run = SendRun(config_json=self.settings, status="running")
            s.add(run)
            s.commit()
            run_id = run.id
        print(f"DEBUG: 运行记录创建完成，ID: {run_id}")

        stats = {"sent": 0, "failed": 0, "total": 0, "run_id": run_id}

        try:
            print("DEBUG: 开始获取可用账号")
            # 获取所有可用的账号（排除frozen、banned等永久不可用状态）
            with self.repo.session() as s:
                now = datetime.utcnow()
                # 获取状态为ok或limited的账号（limited可能恢复）
                ok_accounts = s.query(Account).filter(
                    Account.status.in_(["ok", "limited"])
                ).all()
                print(f"DEBUG: 找到 {len(ok_accounts)} 个可用状态的账号")
                
                # 过滤掉限制期内的账号和永久不可用的账号
                available_accounts = []
                for acc in ok_accounts:
                    # Skip permanently disabled accounts
                    if acc.status in ["frozen", "banned", "revoked", "invalid"]:
                        self.on_log(f"⏭️ 跳过账号 {acc.phone}: 状态={acc.status}")
                        continue
                    
                    
                    if acc.is_limited and acc.limited_until:
                        # 检查是否超过12小时限制期
                        if now < acc.limited_until:
                            # 还在限制期内，跳过
                            continue
                        else:
                            # 限制期已过，恢复账号状态
                            acc.is_limited = False
                            acc.limited_until = None
                            acc.status = "ok"
                            s.commit()
                            self.on_log(f"🔄 账号 {acc.phone} 限制期已过，已恢复发送")
                    
                    available_accounts.append(acc)
                
                if not available_accounts:
                    self.on_log("❌ 没有可用的账号（所有账号都被限制或状态异常）")
                    return stats
                
                account_list = [(a.id, a.phone) for a in available_accounts]
                print(f"DEBUG: 可用账号列表: {account_list}")

            print("DEBUG: 开始获取待发送目标")
            # Get pending targets
            with self.repo.session() as s:
                targets = s.query(Target).filter(Target.status == "pending").all()
                target_list = [(t.id, t.identifier) for t in targets]
                print(f"DEBUG: 找到 {len(target_list)} 个待发送目标")
                
                # 统计所有目标状态
                all_targets = s.query(Target).all()
                status_counts = {}
                for t in all_targets:
                    status = t.status or "unknown"
                    status_counts[status] = status_counts.get(status, 0) + 1
            
            stats["total"] = len(target_list)
            
            # 详细的目标状态说明
            if len(target_list) == 0:
                status_info = ", ".join([f"{status}: {count}" for status, count in status_counts.items()])
                self.on_log(f"📊 并发账号数: {len(account_list)}, 待发送目标: 0")
                self.on_log(f"📋 目标状态统计: {status_info}")
                if "failed" in status_counts:
                    self.on_log("💡 提示: 有发送失败的目标，可使用'重置状态'按钮重新发送")
            else:
                self.on_log(f"📊 并发账号数: {len(account_list)}, 待发送目标: {len(target_list)}")

            print("DEBUG: 开始设置并发参数")
            # 改进的并发逻辑：使用信号量控制真正的并发数量
            per_account = self.settings.get("per", 20)
            concurrency = min(self.settings.get("conc", 6), len(account_list))
            
            self.on_log(f"📊 并发设置: 最大并发数={concurrency}, 单号限制={per_account}")
            print(f"DEBUG: 并发参数设置完成 - 并发数: {concurrency}, 单号限制: {per_account}")
            
            # 创建信号量来控制并发数量
            print("DEBUG: 创建信号量")
            semaphore = asyncio.Semaphore(concurrency)
            print("DEBUG: 信号量创建完成")
            
            # 重新设计：每个账号完全独立发送
            async def account_sender(account_id, phone, assigned_targets):
                """单个账号的独立发送器"""
                sent_count = 0
                failed_count = 0
                total_targets = len(assigned_targets)
                
                # 更新账号发送状态为"正在发送"
                try:
                    with self.repo.session() as s:
                        from ..db.models import Account
                        acc = s.get(Account, account_id)
                        if acc:
                            acc.send_status = "正在发送"
                            s.commit()
                except Exception as e:
                    self.on_log(f"⚠️ 更新账号 {phone} 发送状态失败: {e}")
                
                self.on_log(f"🚀 账号 {phone} 开始独立发送任务 (分配 {total_targets} 个目标)")
                
                try:
                    # 逐个发送分配给该账号的目标
                    for idx, (target_id, target_identifier) in enumerate(assigned_targets, 1):
                        if self.stopped:
                            self.on_log(f"⏸️ 账号 {phone} 已停止 ({idx-1}/{total_targets})")
                            break
                        
                        # 应用时间间隔（第一条消息不等待）
                        if idx > 1:
                            if self.settings.get("random", True):
                                delay = random.uniform(
                                    self.settings.get("min", 15), self.settings.get("max", 15)
                                )
                            else:
                                delay = self.settings.get("fixed", 15)
                            self.on_log(f"⏰ 账号 {phone} 等待 {delay:.1f} 秒...")
                            await asyncio.sleep(delay)
                        
                        self.on_log(f"🚀 账号 {phone} 开始发送 → {target_identifier} ({idx}/{total_targets})")
                        
                        try:
                            # 发送单条消息
                            success, error, is_limited = await asyncio.wait_for(
                                self._send_one_message(account_id, phone, target_identifier, message, image_path, run_id, stats),
                                timeout=60.0  # 60秒超时
                            )
                            
                            if success:
                                sent_count += 1
                                self.on_log(f"✅ 账号 {phone} 完成发送 → {target_identifier} (成功: {sent_count}/{total_targets})")
                            else:
                                failed_count += 1
                                self.on_log(f"❌ 账号 {phone} 发送失败 → {target_identifier}: {error}")
                                
                                # 如果是账号限制，立即停止该账号
                                if is_limited:
                                    self.on_log(f"⚠️ 账号 {phone} 被限制，立即停止该账号")
                                    # Update account status immediately
                                    try:
                                        with self.repo.session() as s:
                                            from ..db.models import Account
                                            acc = s.get(Account, account_id)
                                            if acc:
                                                # Check error type to set appropriate status
                                                if ("frozen" in (error or "").lower() or 
                                                    "invalid" in (error or "").lower() or
                                                    "An invalid Peer was used" in (error or "")):
                                                    acc.status = "frozen"
                                                    acc.is_limited = True
                                                    acc.limited_until = None
                                                    self.on_log(f"🧊 账号 {phone} 状态已更新为冻结")
                                                elif "banned" in (error or "").lower() or "PHONE_NUMBER_BANNED" in (error or "") or "被封禁" in (error or ""):
                                                    acc.status = "banned"
                                                    acc.is_limited = True
                                                    acc.limited_until = None
                                                    self.on_log(f"🚫 账号 {phone} 状态已更新为封禁")
                                                else:
                                                    # Default to limited
                                                    from datetime import timedelta
                                                    acc.is_limited = True
                                                    acc.limited_until = datetime.utcnow() + timedelta(hours=12)
                                                    acc.status = "limited"
                                                    self.on_log(f"⏰ 账号 {phone} 状态已更新为限制")
                                                s.commit()
                                    except Exception as db_e:
                                        self.on_log(f"⚠️ 更新账号 {phone} 状态失败: {db_e}")
                                    
                                    break
                                
                        except Exception as e:
                            failed_count += 1
                            self.on_log(f"❌ 账号 {phone} 发送异常 → {target_identifier}: {e}")
                            
                            # 检查是否是账号限制
                            if "Too many requests" in str(e) or "FLOOD_WAIT" in str(e):
                                self.on_log(f"⚠️ 账号 {phone} 被限制，立即停止该账号")
                                
                                # 更新账号状态为限制
                                try:
                                    with self.repo.session() as s:
                                        from ..db.models import Account
                                        from datetime import timedelta
                                        acc = s.get(Account, account_id)
                                        if acc:
                                            acc.is_limited = True
                                            acc.limited_until = datetime.utcnow() + timedelta(hours=12)
                                            acc.status = "limited"
                                            s.commit()
                                            self.on_log(f"🔄 账号状态已更新为限制")
                                except Exception as db_e:
                                    self.on_log(f"⚠️ 更新账号限制状态失败: {db_e}")
                                
                                break
                    
                    self.on_log(f"✅ 账号 {phone} 独立发送任务完成 - 成功: {sent_count}, 失败: {failed_count}, 总计: {total_targets}")
                    
                except Exception as e:
                    self.on_log(f"❌ 账号 {phone} 发送任务异常: {e}")
                    import traceback
                    self.on_log(f"异常详情: {traceback.format_exc()}")
                finally:
                    # 更新账号发送状态为"等待发送"
                    try:
                        with self.repo.session() as s:
                            from ..db.models import Account
                            acc = s.get(Account, account_id)
                            if acc:
                                acc.send_status = "等待发送"
                                s.commit()
                    except Exception as e:
                        self.on_log(f"⚠️ 更新账号 {phone} 发送状态失败: {e}")
            
            # 分配目标给每个账号
            account_targets = {}
            targets_per_account = len(target_list) // len(account_list)
            remaining_targets = len(target_list) % len(account_list)
            
            start_idx = 0
            for i, (account_id, phone) in enumerate(account_list):
                # 计算该账号分配的目标数量
                count = targets_per_account + (1 if i < remaining_targets else 0)
                end_idx = start_idx + count
                
                # 分配目标
                assigned_targets = target_list[start_idx:end_idx]
                account_targets[account_id] = assigned_targets
                
                self.on_log(f"📋 账号 {phone} 分配到 {len(assigned_targets)} 个目标")
                start_idx = end_idx
            
            # 使用动态任务调度，确保所有账号都被使用
            account_queue = list(enumerate(account_list))  # 创建账号队列
            active_workers = {}  # 活跃的worker: {task: (account_id, phone)}
            
            # 启动初始并发任务
            for i in range(min(concurrency, len(account_queue))):
                idx, (account_id, phone) = account_queue.pop(0)
                assigned_targets = account_targets[account_id]
                if assigned_targets:
                    worker = asyncio.create_task(account_sender(account_id, phone, assigned_targets))
                    active_workers[worker] = (account_id, phone)
                    self.on_log(f"🚀 创建发送任务: 账号 {phone} (并发 {len(active_workers)}/{concurrency})")
            
            # 动态管理任务：当一个任务完成时，启动下一个账号
            while active_workers:
                # 等待任意一个任务完成
                done, pending = await asyncio.wait(
                    active_workers.keys(), 
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # 处理完成的任务
                for completed_task in done:
                    account_id, phone = active_workers.pop(completed_task)
                    try:
                        await completed_task  # 确保异常被处理
                    except Exception as e:
                        self.on_log(f"⚠️ 账号 {phone} 任务异常: {e}")
                    
                    # 如果还有待发送的账号，启动下一个
                    if account_queue:
                        idx, (next_account_id, next_phone) = account_queue.pop(0)
                        next_targets = account_targets[next_account_id]
                        if next_targets:
                            self.on_log(f"🔄 启动备用账号: {next_phone} (剩余 {len(account_queue)} 个账号)")
                            new_worker = asyncio.create_task(
                                account_sender(next_account_id, next_phone, next_targets)
                            )
                            active_workers[new_worker] = (next_account_id, next_phone)
            
            self.on_log(f"✅ 所有 {len(account_list)} 个账号已完成发送")
            
            print("DEBUG: 所有账号发送任务完成")
            
            # 清空任务列表
            self._tasks.clear()
            self.on_log("🔧 所有发送任务已清理")
            print("DEBUG: 任务清理完成")

            # Update run summary
            with self.repo.session() as s:
                run = s.get(SendRun, run_id)
                if run:
                    run.status = "stopped" if self.stopped else "completed"
                    run.summary = f"Sent: {stats['sent']}, Failed: {stats['failed']}, Total: {stats['total']}"
                    s.commit()

        except Exception as e:
            with self.repo.session() as s:
                run = s.get(SendRun, run_id)
                if run:
                    run.status = "error"
                    run.summary = str(e)
                    s.commit()
            raise

        return stats

    async def _send_one_message(
        self,
        account_id: int,
        phone: str,
        target_identifier: str,
        message: str,
        image_path: str | None,
        run_id: int,
        stats: dict,
    ) -> tuple[bool, str | None, bool]:
        """发送单条消息，返回 (success, error_msg, is_limited)"""
        client = None
        
        try:
            # 登录账号 - 使用手机号而不是索引
            try:
                # 添加调试信息
                self.on_log(f"🔍 尝试登录账号 {phone} (ID: {account_id})")
                client, _ = await asyncio.wait_for(
                    login_account_by_phone(phone), 
                    timeout=30.0
                )
            except Exception as e:
                error_msg = f"登录失败: {e}"
                self.on_log(f"❌ {error_msg}")
                return (False, error_msg, False)
            
            if not client:
                return (False, "登录失败: 无法获取客户端", False)
            
            # 发送消息
            success, error, is_limited = await self._send_one(
                client, target_identifier, message, image_path, account_id
            )
            
            # 更新数据库状态
            try:
                with self.repo.session() as s:
                    from ..db.models import Target, SendLog, Account
                    import pytz
                    
                    # 创建发送日志
                    log = SendLog(
                        run_id=run_id,
                        account_id=account_id,
                        target_identifier=target_identifier,
                        status="sent" if success else "failed",
                        error=error,
                    )
                    s.add(log)
                    
                    # 更新目标状态
                    target = s.query(Target).filter(Target.identifier == target_identifier).first()
                    if target:
                        if success:
                            target.status = "sent"
                            target.last_sent_at = datetime.utcnow()
                        else:
                            target.status = "failed"
                            target.fail_reason = error
                            target.last_sent_at = datetime.utcnow()
                    
                    # 更新账号发送计数
                    acc = s.get(Account, account_id)
                    if acc:
                        shanghai_tz = pytz.timezone('Asia/Shanghai')
                        today = datetime.now(shanghai_tz).strftime("%Y-%m-%d")
                        
                        if acc.last_sent_date != today:
                            acc.daily_sent_count = 0
                            acc.last_sent_date = today
                        
                        acc.daily_sent_count += 1
                        acc.total_sent_count += 1
                    
                    s.commit()
            except Exception as db_e:
                self.on_log(f"⚠️ 更新数据库状态失败: {db_e}")
            
            # 更新统计
            if success:
                stats["sent"] += 1
            else:
                stats["failed"] += 1
            
            self.on_progress(stats)
            
            return (success, error, is_limited)
            
        finally:
            # 确保客户端连接被正确关闭
            if client:
                try:
                    if client.is_connected():
                        await asyncio.wait_for(client.disconnect(), timeout=10.0)
                except Exception as e:
                    self.on_log(f"⚠️ 关闭账号 {phone} 客户端连接失败: {e}")

    async def _send_for_account(
        self,
        account_id: int,
        phone: str,
        targets: list[tuple[int, str]],
        message: str,
        image_path: str | None,
        run_id: int,
        stats: dict,
    ) -> bool:
        """Send messages for one account. Returns True if login succeeded, False otherwise."""
        account_status = "ok"
        account_error = None
        client = None
        sent_count = 0
        failed_count = 0
        total_for_account = len(targets)
        
        try:
            # 登录账号，添加超时保护（有锁保护，不需要重试）
            try:
                # 添加调试信息
                self.on_log(f"🔍 尝试登录账号 {phone} (ID: {account_id})")
                client, _ = await asyncio.wait_for(
                    login_account_by_phone(phone), 
                    timeout=30.0  # 30秒超时
                )
            except asyncio.TimeoutError:
                account_status = "error"
                account_error = "登录超时"
                self.on_log(f"❌ 账号 {phone} 登录超时")
            except Exception as e:
                account_status = "error"
                account_error = str(e)
                self.on_log(f"❌ 账号 {phone} 登录失败: {e}")
            
            if not client:
                # 更新账号状态
                try:
                    with self.repo.session() as s:
                        acc = s.get(Account, account_id)
                        if acc:
                            acc.status = "login_failed"
                            acc.last_login_at = datetime.utcnow()
                            s.commit()
                except Exception as db_e:
                    self.on_log(f"⚠️ 更新账号状态失败: {db_e}")
                return False  # 登录失败，返回False

            # 发送消息
            for idx, (target_id, identifier) in enumerate(targets, 1):
                if self.stopped:
                    self.on_log(f"⏸️ 账号 {phone} 已停止 ({idx-1}/{total_for_account})")
                    break

                try:
                    # Apply delay (skip for first message)
                    if idx > 1:
                        if self.settings.get("random", True):
                            delay = random.uniform(
                                self.settings.get("min", 15), self.settings.get("max", 15)
                            )
                        else:
                            delay = self.settings.get("fixed", 15)
                        await asyncio.sleep(delay)

                    # Send with timeout
                    success, error, is_limited = await asyncio.wait_for(
                        self._send_one(client, identifier, message, image_path, account_id),
                        timeout=60.0  # 60秒超时
                    )
                    
                    # 如果账号被限制或出现严重异常，更新账号状态并立即停止该账号的后续发送
                    if is_limited:
                        try:
                            with self.repo.session() as s:
                                acc = s.get(Account, account_id)
                                if acc:
                                    # 根据错误类型设置不同的状态
                                    if "被封禁" in error or "PHONE_NUMBER_BANNED" in error:
                                        acc.status = "banned"
                                        acc.is_limited = True
                                        acc.limited_until = None  # 封禁是永久的
                                        account_status = "banned"  # 更新本地状态变量
                                        self.on_log(f"🚫 账号 {phone} 被封禁，立即停止该账号的后续发送")
                                    elif "手机号无效" in error or "PHONE_NUMBER_INVALID" in error:
                                        acc.status = "invalid"
                                        acc.is_limited = True
                                        acc.limited_until = None  # 无效是永久的
                                        account_status = "invalid"  # 更新本地状态变量
                                        self.on_log(f"❌ 账号 {phone} 手机号无效，立即停止该账号的后续发送")
                                    elif "会话被撤销" in error or "SESSION_REVOKED" in error:
                                        acc.status = "revoked"
                                        acc.is_limited = True
                                        acc.limited_until = None  # 会话撤销需要重新登录
                                        account_status = "revoked"  # 更新本地状态变量
                                        self.on_log(f"🔑 账号 {phone} 会话被撤销，立即停止该账号的后续发送")
                                    else:
                                        # 普通的请求限制
                                        from datetime import timedelta
                                        acc.is_limited = True
                                        acc.limited_until = datetime.utcnow() + timedelta(hours=12)
                                        acc.status = "limited"
                                        account_status = "limited"  # 更新本地状态变量
                                        self.on_log(f"⏰ 账号 {phone} 因请求频繁被限制12小时，立即停止该账号的后续发送")
                                    s.commit()
                        except Exception as db_e:
                            self.on_log(f"⚠️ 更新账号状态失败: {db_e}")
                        
                        # 立即停止该账号的后续发送
                        self.on_log(f"🛑 账号 {phone} 出现异常，停止发送剩余 {total_for_account - idx} 个目标")
                        break

                    # Log result
                    status_icon = "✅" if success else "❌"
                    self.on_log(f"  {status_icon} [{phone}] → {identifier}")

                    # Save to database with error handling
                    try:
                        with self.repo.session() as s:
                            log = SendLog(
                                run_id=run_id,
                                account_id=account_id,
                                target_identifier=identifier,
                                status="sent" if success else "failed",
                                error=error,
                            )
                            s.add(log)
                            # Update target
                            t = s.get(Target, target_id)
                            if t:
                                if success:
                                    t.status = "sent"
                                    t.last_sent_at = datetime.utcnow()
                                else:
                                    t.status = "failed"
                                    t.fail_reason = error
                                    t.last_sent_at = datetime.utcnow()  # 失败也要记录发送时间
                            
                            # 立即更新账号的每日发送计数
                            acc = s.get(Account, account_id)
                            if acc:
                                shanghai_tz = pytz.timezone('Asia/Shanghai')
                                today = datetime.now(shanghai_tz).strftime("%Y-%m-%d")
                                
                                # 如果是新的一天，重置计数
                                if acc.last_sent_date != today:
                                    acc.daily_sent_count = 0
                                    acc.last_sent_date = today
                                
                                # 增加发送计数（无论成功失败都计数）
                                acc.daily_sent_count += 1
                                acc.total_sent_count += 1
                            
                            s.commit()
                    except Exception as db_e:
                        self.on_log(f"⚠️ 保存发送记录失败: {db_e}")

                    if success:
                        stats["sent"] += 1
                        sent_count += 1
                    else:
                        stats["failed"] += 1
                        failed_count += 1

                    self.on_progress(stats)
                    
                except asyncio.TimeoutError:
                    error_msg = f"发送超时"
                    self.on_log(f"  ⏰ [{phone}] → {identifier} - {error_msg}")
                    stats["failed"] += 1
                    failed_count += 1
                    self.on_progress(stats)
                    
                    # 保存超时记录
                    try:
                        with self.repo.session() as s:
                            log = SendLog(
                                run_id=run_id,
                                account_id=account_id,
                                target_identifier=identifier,
                                status="failed",
                                error=error_msg,
                            )
                            s.add(log)
                            t = s.get(Target, target_id)
                            if t:
                                t.status = "failed"
                                t.fail_reason = error_msg
                                t.last_sent_at = datetime.utcnow()
                            
                            # 更新账号的每日发送计数
                            acc = s.get(Account, account_id)
                            if acc:
                                shanghai_tz = pytz.timezone('Asia/Shanghai')
                                today = datetime.now(shanghai_tz).strftime("%Y-%m-%d")
                                
                                if acc.last_sent_date != today:
                                    acc.daily_sent_count = 0
                                    acc.last_sent_date = today
                                
                                acc.daily_sent_count += 1
                                acc.total_sent_count += 1
                            
                            s.commit()
                    except Exception as db_e:
                        self.on_log(f"⚠️ 保存超时记录失败: {db_e}")
                        
                except Exception as e:
                    error_msg = f"发送异常: {str(e)}"
                    self.on_log(f"  ❌ [{phone}] → {identifier} - {error_msg}")
                    stats["failed"] += 1
                    failed_count += 1
                    self.on_progress(stats)
                    
                    # 保存异常记录
                    try:
                        with self.repo.session() as s:
                            log = SendLog(
                                run_id=run_id,
                                account_id=account_id,
                                target_identifier=identifier,
                                status="failed",
                                error=error_msg,
                            )
                            s.add(log)
                            t = s.get(Target, target_id)
                            if t:
                                t.status = "failed"
                                t.fail_reason = error_msg
                                t.last_sent_at = datetime.utcnow()
                            
                            # 更新账号的每日发送计数
                            acc = s.get(Account, account_id)
                            if acc:
                                shanghai_tz = pytz.timezone('Asia/Shanghai')
                                today = datetime.now(shanghai_tz).strftime("%Y-%m-%d")
                                
                                if acc.last_sent_date != today:
                                    acc.daily_sent_count = 0
                                    acc.last_sent_date = today
                                
                                acc.daily_sent_count += 1
                                acc.total_sent_count += 1
                            
                            s.commit()
                    except Exception as db_e:
                        self.on_log(f"⚠️ 保存异常记录失败: {db_e}")

        finally:
            # 确保客户端连接被正确关闭
            if client:
                try:
                    # 先检查客户端是否已连接
                    if client.is_connected():
                        await asyncio.wait_for(client.disconnect(), timeout=10.0)
                        self.on_log(f"✅ 账号 {phone} 客户端连接已关闭")
                    else:
                        self.on_log(f"ℹ️ 账号 {phone} 客户端未连接，无需关闭")
                except asyncio.TimeoutError:
                    self.on_log(f"⚠️ 账号 {phone} 客户端断开连接超时")
                except Exception as e:
                    self.on_log(f"⚠️ 关闭账号 {phone} 客户端连接失败: {e}")
            
            # 更新账号状态（不重复更新计数，因为每次发送时已经更新了）
            try:
                with self.repo.session() as s:
                    acc = s.get(Account, account_id)
                    if acc:
                        acc.status = account_status
                        acc.last_login_at = datetime.utcnow()
                        s.commit()
                        self.on_log(f"✅ 账号 {phone} 状态已更新: {account_status}")
            except Exception as db_e:
                self.on_log(f"⚠️ 更新账号 {phone} 状态失败: {db_e}")
            
            self.on_log(f"✅ 账号 {phone} 完成 - 成功: {sent_count}, 失败: {failed_count}")
            return True  # 登录成功，返回True

    async def _send_one(
        self, client, identifier: str, message: str, image_path: str | None, account_id: int
    ) -> tuple[bool, str | None, bool]:
        """Send to one recipient. Returns (success, error_msg, is_limited)."""
        try:
            # 如果是手机号，先尝试添加为联系人
            if identifier.startswith('+') or identifier.isdigit():
                # 标准化手机号格式
                clean_phone = identifier.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
                
                # 确保手机号以+开头
                if not clean_phone.startswith('+'):
                    # 如果是纯数字，尝试添加+号
                    if clean_phone.isdigit():
                        clean_phone = '+' + clean_phone
                
                # 先尝试添加为联系人
                try:
                    self.on_log(f"📞 正在添加联系人: {clean_phone}")
                    
                    # 创建联系人对象
                    contact = InputPhoneContact(
                        client_id=random.randint(1000000, 9999999),  # 随机client_id
                        phone=clean_phone,
                        first_name="User",
                        last_name=""
                    )
                    
                    result = await client(ImportContactsRequest([contact]))
                    
                    # 详细检查添加结果
                    self.on_log(f"🔍 联系人添加结果: {result}")
                    
                    # 检查不同的成功条件
                    success = False
                    if hasattr(result, 'imported') and result.imported:
                        success = True
                    elif hasattr(result, 'users') and result.users:
                        # 如果有用户信息返回，也算成功
                        success = True
                    elif hasattr(result, 'retry_contacts') and not result.retry_contacts:
                        # 没有重试联系人，可能已经存在
                        success = True
                    
                    if success:
                        self.on_log(f"✅ 联系人添加成功: {clean_phone}")
                        # 等待一下让联系人同步
                        await asyncio.sleep(2)
                    else:
                        self.on_log(f"⚠️ 联系人添加失败: {clean_phone} (可能已存在或无效)")
                        # 即使添加失败，也尝试发送
                        
                except Exception as add_e:
                    error_str = str(add_e)
                    self.on_log(f"❌ 添加联系人异常: {clean_phone} - {add_e}")
                    
                    # Check if account is frozen
                    if "frozen account" in error_str.lower():
                        self.on_log(f"🧊 账号被冻结，立即停止")
                        # Update account status immediately
                        try:
                            with self.repo.session() as s:
                                from ..db.models import Account
                                acc = s.get(Account, account_id)
                                if acc:
                                    acc.status = "frozen"
                                    acc.is_limited = True
                                    acc.limited_until = None
                                    s.commit()
                        except Exception as db_e:
                            self.on_log(f"⚠️ 更新账号冻结状态失败: {db_e}")
                        
                        # Stop trying to send
                        return (False, f"账号被冻结: {error_str}", True)
                    
                    # 即使添加失败，也尝试发送
                
                identifier = clean_phone
            
            # 发送消息
            try:
                if image_path:
                    await client.send_file(entity=identifier, file=image_path, caption=message)
                else:
                    await client.send_message(entity=identifier, message=message)
                return (True, None, False)
            except Exception as send_e:
                # 如果发送失败，可能是联系人问题，尝试重新添加
                if "Cannot find any entity" in str(send_e) and (identifier.startswith('+') or identifier.isdigit()):
                    self.on_log(f"🔄 发送失败，尝试重新添加联系人: {identifier}")
                    try:
                        # 再次尝试添加联系人
                        contact = InputPhoneContact(
                            client_id=random.randint(1000000, 9999999),
                            phone=identifier,
                            first_name="User",
                            last_name=""
                        )
                        await client(ImportContactsRequest([contact]))
                        await asyncio.sleep(2)
                        
                        # 再次尝试发送
                        if image_path:
                            await client.send_file(entity=identifier, file=image_path, caption=message)
                        else:
                            await client.send_message(entity=identifier, message=message)
                        return (True, None, False)
                    except Exception as retry_e:
                        self.on_log(f"❌ 重试发送也失败: {identifier} - {retry_e}")
                        raise send_e  # 抛出原始错误
                else:
                    raise send_e  # 抛出原始错误
        except FloodWaitError as e:
            # 遇到FloodWaitError，直接标记账号需要限制，不重试
            error_msg = f"Too many requests (wait {e.seconds}s)"
            self.on_log(f"⚠️ 账号遇到请求限制: {error_msg}")
            
            # 立即更新账号状态为限制
            try:
                with self.repo.session() as s:
                    from ..db.models import Account
                    from datetime import timedelta
                    acc = s.get(Account, account_id)
                    if acc:
                        acc.is_limited = True
                        acc.limited_until = datetime.utcnow() + timedelta(hours=12)
                        acc.status = "limited"
                        s.commit()
                        self.on_log(f"🔄 账号状态已更新为限制")
            except Exception as db_e:
                self.on_log(f"⚠️ 更新账号限制状态失败: {db_e}")
            
            return (False, error_msg, True)  # 失败且需要限制账号
        except (UserPrivacyRestrictedError, UsernameInvalidError) as e:
            return (False, f"Privacy/Invalid: {e}", False)
        except Exception as e:
            # 检查是否是其他类型的"Too many requests"错误
            error_str = str(e)
            if "Too many requests" in error_str or "FLOOD_WAIT" in error_str:
                self.on_log(f"⚠️ 账号遇到请求限制: {error_str}")
                
                # 立即更新账号状态为限制
                try:
                    with self.repo.session() as s:
                        from ..db.models import Account
                        from datetime import timedelta
                        acc = s.get(Account, account_id)
                        if acc:
                            acc.is_limited = True
                            acc.limited_until = datetime.utcnow() + timedelta(hours=12)
                            acc.status = "limited"
                            s.commit()
                            self.on_log(f"🔄 账号状态已更新为限制")
                except Exception as db_e:
                    self.on_log(f"⚠️ 更新账号限制状态失败: {db_e}")
                
                return (False, error_str, True)  # 失败且需要限制账号
            elif "frozen account" in error_str.lower() or "ACCOUNT_FROZEN" in error_str:
                self.on_log(f"🧊 账号被冻结: {error_str}")
                # Update status
                try:
                    with self.repo.session() as s:
                        from ..db.models import Account
                        acc = s.get(Account, account_id)
                        if acc:
                            acc.status = "frozen"
                            acc.is_limited = True
                            acc.limited_until = None
                            s.commit()
                except Exception as db_e:
                    self.on_log(f"⚠️ 更新账号冻结状态失败: {db_e}")
                
                return (False, f"账号被冻结: {error_str}", True)
            elif "PHONE_NUMBER_BANNED" in error_str or "PHONE_BANNED" in error_str:
                # 手机号被封禁
                self.on_log(f"🚫 账号被封禁: {error_str}")
                return (False, f"账号被封禁: {error_str}", True)  # 需要停止该账号
            elif "PHONE_NUMBER_INVALID" in error_str:
                # 手机号无效
                self.on_log(f"❌ 手机号无效: {error_str}")
                return (False, f"手机号无效: {error_str}", True)  # 需要停止该账号
            elif "SESSION_REVOKED" in error_str or "AUTH_KEY_INVALID" in error_str:
                # 会话被撤销
                self.on_log(f"🔑 会话被撤销: {error_str}")
                return (False, f"会话被撤销: {error_str}", True)  # 需要停止该账号
            elif "Cannot find any entity" in error_str or "ENTITY_NOT_FOUND" in error_str:
                # 实体未找到错误，通常是联系人问题
                if identifier.startswith('+') or identifier.isdigit():
                    return (False, f"联系人未找到: {identifier} (可能需要手动添加)", False)
                else:
                    return (False, f"用户未找到: {identifier}", False)
            elif "invalid" in error_str.lower():
                # 包含 "invalid" 关键词的错误 - 通常是账号被封/冻结
                # 更新账号状态为冻结
                try:
                    with self.repo.session() as s:
                        from ..db.models import Account
                        acc = s.get(Account, account_id)
                        if acc:
                            acc.status = "frozen"
                            acc.is_limited = True
                            acc.limited_until = None
                            s.commit()
                            self.on_log(f"🧊 账号状态已更新为冻结")
                except Exception as db_e:
                    self.on_log(f"⚠️ 更新账号冻结状态失败: {db_e}")
                
                return (False, error_str, True)  # 返回原始错误信息，停止该账号
            return (False, str(e), False)

    def stop(self):
        """Stop sending."""
        self.stopped = True
        # 取消所有运行中的任务
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self.on_log("🛑 发送引擎已停止，正在取消所有任务...")

