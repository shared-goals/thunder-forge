from __future__ import annotations

from services.config_service import load_config


def get_admin_telegram_ids() -> set[int]:
    cfg = load_config()
    return set(cfg.access.admin_telegram_ids or [])


def is_admin_telegram_id(user_id: int) -> bool:
    admins = get_admin_telegram_ids()
    return bool(admins) and user_id in admins
