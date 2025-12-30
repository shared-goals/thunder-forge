from __future__ import annotations

from fastapi import APIRouter, Depends

from services.auth_service import TelegramUser, get_authenticated_user
from services.config_service import load_config
from services.monitor_service import cluster_status_as_dict

router = APIRouter()


@router.post("/me")
async def post_me(user: TelegramUser = Depends(get_authenticated_user)):
    return {"user": user.model_dump()}


@router.post("/status")
async def post_status(_: TelegramUser = Depends(get_authenticated_user)):
    cfg = load_config()
    return cluster_status_as_dict(cfg)
