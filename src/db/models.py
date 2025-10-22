from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Column,
    String,
    Integer,
    Boolean,
    DateTime,
    Text,
    JSON,
    create_engine,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(32), unique=True, index=True, nullable=False)
    session_file = Column(String(255), nullable=True)
    is_valid = Column(Boolean, default=True)
    status = Column(String(64), default="unknown")
    last_login_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    # 新增字段：每日发送统计
    daily_sent_count = Column(Integer, default=0)  # 今日已发送数量（包括成功和失败）
    last_sent_date = Column(String(10), nullable=True)  # 最后发送日期 YYYY-MM-DD
    total_sent_count = Column(Integer, default=0)  # 总发送数量（包括成功和失败）
    # 新增字段：限制状态
    is_limited = Column(Boolean, default=False)  # 是否被限制发送
    limited_until = Column(DateTime, nullable=True)  # 限制到期时间
    # 新增字段：发送状态
    send_status = Column(String(32), default="未启用")  # 发送状态：未启用/正在发送/等待发送


class Target(Base):
    __tablename__ = "targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    identifier = Column(String(128), unique=True, index=True, nullable=False)  # @user / +phone / user_id
    source = Column(String(64), default="file")
    status = Column(String(32), default="pending")  # pending/sent/failed
    last_sent_at = Column(DateTime, nullable=True)
    fail_reason = Column(String(255), nullable=True)


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_or_username = Column(String(255), unique=True, index=True, nullable=False)
    joined = Column(Boolean, default=False)
    fetched = Column(Boolean, default=False)
    last_fetched_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)


class GroupMember(Base):
    __tablename__ = "group_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, index=True, nullable=False)
    identifier = Column(String(128), nullable=False)
    is_bot = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("group_id", "identifier", name="uq_group_member"),
    )


class SendRun(Base):
    __tablename__ = "send_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    config_json = Column(JSON, nullable=True)
    status = Column(String(32), default="created")
    summary = Column(Text, nullable=True)


class SendLog(Base):
    __tablename__ = "send_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, index=True, nullable=False)
    account_id = Column(Integer, index=True, nullable=True)
    target_identifier = Column(String(128), nullable=False)
    status = Column(String(32), default="sent")
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)
    latency_ms = Column(Integer, nullable=True)


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(64), unique=True, index=True, nullable=False)
    value = Column(Text, nullable=True)


def create_session(db_path: Path, create_dirs: bool = False):
    if create_dirs:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    
    engine = create_engine(f"sqlite:///{db_path}", echo=False, future=True)
    
    if create_dirs:
        Base.metadata.create_all(engine)
    
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


