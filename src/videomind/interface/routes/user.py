"""用户配置路由 —— auth 前的 dev bootstrap。

GET /api/user/config  → 取（自动 bootstrap 一个 dev 用户与其配置行）
PUT /api/user/config  → 更新配置

auth 体系（user / membership / api_key）尚未接线，所有需要 user_id 的操作
（Agent 分析、RAG 会话）当前都依赖这个 dev bootstrap 用户。实现线性、可移除：
auth 接好后改为从 token 解析 user_id，删掉 _get_or_create_dev_user 即可。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage.database import AsyncSessionLocal, get_db

router = APIRouter(tags=["user"], prefix="/user")


DEV_USERNAME = "dev"


# ──────────────────────────── Pydantic Schema ────────────────────────────


class UserConfigResponse(BaseModel):
    user_id: str
    theme: str
    default_model: str | None
    language: str


class UserConfigUpdate(BaseModel):
    theme: str | None = Field(None)
    default_model: str | None = Field(None)
    language: str | None = Field(None)


# ──────────────────────────── bootstrap ────────────────────────────


async def _get_or_create_dev_user() -> m.User:
    """幂等获取或创建 dev 用户（固定 username/email，固定占位 password_hash）。

    auth 接线后删除本函数，user_id 改由 JWT 解析。
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(m.User).where(m.User.username == DEV_USERNAME))
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        user = m.User(
            username=DEV_USERNAME,
            email="dev@videomind.local",
            password_hash="dev-no-auth-placeholder",  # 占位 hash，auth 接线前不参与登录校验
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _get_or_create_dev_config(user_id: uuid.UUID) -> m.UserConfig:
    """幂等获取或创建该 dev 用户的配置行。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(m.UserConfig).where(m.UserConfig.user_id == user_id)
        )
        cfg = result.scalar_one_or_none()
        if cfg is not None:
            return cfg

        cfg = m.UserConfig(user_id=user_id, theme="system", language="zh-CN")
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
        return cfg


# ──────────────────────────── Routes ────────────────────────────


@router.get("/config", response_model=UserConfigResponse)
async def get_user_config(db: AsyncSession = Depends(get_db)) -> UserConfigResponse:
    user = await _get_or_create_dev_user()
    cfg = await _get_or_create_dev_config(user.id)
    return UserConfigResponse(
        user_id=str(user.id),
        theme=cfg.theme,
        default_model=cfg.default_model,
        language=cfg.language,
    )


@router.put("/config", response_model=UserConfigResponse)
async def update_user_config(
    update: UserConfigUpdate, db: AsyncSession = Depends(get_db)
) -> UserConfigResponse:
    user = await _get_or_create_dev_user()
    result = await db.execute(
        select(m.UserConfig).where(m.UserConfig.user_id == user.id)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = m.UserConfig(user_id=user.id, theme="system", language="zh-CN")
        db.add(cfg)

    if update.theme is not None:
        if update.theme not in ("light", "dark", "system"):
            raise HTTPException(status_code=400, detail="theme 必须为 light|dark|system")
        cfg.theme = update.theme
    if update.default_model is not None:
        cfg.default_model = update.default_model or None
    if update.language is not None:
        cfg.language = update.language

    await db.commit()
    await db.refresh(cfg)
    return UserConfigResponse(
        user_id=str(user.id),
        theme=cfg.theme,
        default_model=cfg.default_model,
        language=cfg.language,
    )
