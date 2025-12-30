from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    bind: str = "127.0.0.1"
    port: int = 8000
    reload: bool = True


class TelegramConfig(BaseModel):
    bot_token: str


class SSHSettings(BaseModel):
    connect_timeout_seconds: float = 1.0
    batch_mode: bool = True


class MonitorSettings(BaseModel):
    ssh_port: int = 22
    ollama_port: int = 11434


class HostsSyncSettings(BaseModel):
    managed_block_start: str = "# BEGIN thunder-forge"
    managed_block_end: str = "# END thunder-forge"


class AccessSettings(BaseModel):
    admin_telegram_ids: list[int] = Field(default_factory=list)


class TBIPv4(BaseModel):
    address: str
    netmask: str
    router: str = ""


class TBNet(BaseModel):
    service_name: str
    ipv4: TBIPv4


class Node(BaseModel):
    name: str
    ssh_user: str
    wifi_ip: str
    service_manager: Literal["brew", "systemd"]

    # Optional overrides
    ssh_host: Optional[str] = None
    tb_ip: Optional[str] = None

    ollama_service: str = "ollama"
    models: list[str] = Field(default_factory=list)
    tbnet: Optional[TBNet] = None


class FleetSettings(BaseModel):
    ssh: SSHSettings = Field(default_factory=SSHSettings)
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)
    hosts_sync: HostsSyncSettings = Field(default_factory=HostsSyncSettings)


class TFConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    telegram: TelegramConfig
    access: AccessSettings = Field(default_factory=AccessSettings)
    settings: FleetSettings = Field(default_factory=FleetSettings)
    nodes: list[Node]

    mini_app_url: str = "http://127.0.0.1:8000/mini-app/"

    # Security: Telegram initData max age
    tma_max_age_seconds: int = 86400


def get_config_path() -> str:
    # Only env we keep: which single config file to use.
    return os.environ.get("TF_CONFIG_PATH", "tf.yml")


@lru_cache(maxsize=1)
def load_config(path: Optional[str] = None) -> TFConfig:
    path = path or get_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Missing config file: {path}. Create tf.yml (or set TF_CONFIG_PATH)."
        ) from exc
    return TFConfig.model_validate(data)
