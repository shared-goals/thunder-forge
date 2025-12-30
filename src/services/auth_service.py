from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Optional
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request
from pydantic import BaseModel

from services.access_service import get_admin_telegram_ids
from services.config_service import load_config


class TelegramUser(BaseModel):
    id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


def _extract_init_data_raw_from_headers(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("tma "):
        return auth[4:].strip()

    init_data = request.headers.get("X-Telegram-Init-Data")
    if init_data:
        return init_data.strip()

    return None


def _extract_init_data_raw_from_body(body: Any) -> Optional[str]:
    if not isinstance(body, dict):
        return None

    for key in ("initDataRaw", "initData", "init_data_raw", "init_data"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def _compute_telegram_hash(init_data_raw: str, bot_token: str) -> str:
    pairs = parse_qsl(init_data_raw, keep_blank_values=True)
    data: dict[str, str] = {k: v for (k, v) in pairs}

    received_hash = data.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing init data hash")

    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data.keys()))

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid init data signature")

    return received_hash


def _parse_user_from_init_data(init_data_raw: str) -> TelegramUser:
    data = dict(parse_qsl(init_data_raw, keep_blank_values=True))
    user_json = data.get("user")
    if not user_json:
        raise HTTPException(status_code=401, detail="Missing user in init data")

    try:
        user_dict = json.loads(user_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail="Invalid user JSON in init data") from exc

    try:
        return TelegramUser.model_validate(user_dict)
    except Exception as exc:  # pydantic validation
        raise HTTPException(status_code=401, detail="Invalid user in init data") from exc


def _enforce_auth_date(init_data_raw: str) -> None:
    data = dict(parse_qsl(init_data_raw, keep_blank_values=True))
    auth_date_raw = data.get("auth_date")
    if not auth_date_raw:
        raise HTTPException(status_code=401, detail="Missing auth_date in init data")

    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid auth_date in init data") from exc

    max_age_seconds = int(load_config().tma_max_age_seconds)
    now = int(time.time())
    if auth_date > now + 60:
        raise HTTPException(status_code=401, detail="init data auth_date is in the future")
    if now - auth_date > max_age_seconds:
        raise HTTPException(status_code=401, detail="init data is too old")


async def get_authenticated_user(request: Request) -> TelegramUser:
    """FastAPI dependency.

    - Extracts Telegram init data in the required precedence.
    - Verifies signature using config.telegram.bot_token.
    - Enforces admin allowlist via config.access.admin_telegram_ids.
    """

    init_data_raw = _extract_init_data_raw_from_headers(request)

    if not init_data_raw:
        try:
            body = await request.json()
        except Exception:
            body = None
        init_data_raw = _extract_init_data_raw_from_body(body)

    if not init_data_raw:
        raise HTTPException(status_code=401, detail="Missing Telegram init data")

    bot_token = load_config().telegram.bot_token

    _compute_telegram_hash(init_data_raw, bot_token)
    _enforce_auth_date(init_data_raw)

    user = _parse_user_from_init_data(init_data_raw)

    admins = get_admin_telegram_ids()
    if not admins or user.id not in admins:
        raise HTTPException(status_code=403, detail="Forbidden")

    return user
