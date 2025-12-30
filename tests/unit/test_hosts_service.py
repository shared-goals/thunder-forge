from __future__ import annotations

from services.config_service import HostsSyncSettings, TFConfig
from services.hosts_service import build_hosts_block, upsert_managed_hosts_block


def test_build_hosts_block_contains_wifi_and_tb_lines():
    cfg = TFConfig.model_validate(
        {
            "telegram": {"bot_token": "test-token"},
            "settings": {
                "hosts_sync": {
                    "managed_block_start": "# BEGIN thunder-forge",
                    "managed_block_end": "# END thunder-forge",
                }
            },
            "nodes": [
                {
                    "name": "msm1",
                    "ssh_user": "u",
                    "ssh_host": "1.2.3.4",
                    "wifi_ip": "192.168.1.101",
                    "tb_ip": "172.16.10.2",
                    "service_manager": "brew",
                    "models": [],
                }
            ],
        }
    )

    block = build_hosts_block(cfg).block
    assert "# BEGIN thunder-forge" in block
    assert "192.168.1.101 msm1" in block
    assert "172.16.10.2 msm1-tb" in block
    assert "# END thunder-forge" in block


def test_upsert_replaces_existing_block():
    hosts = "127.0.0.1 localhost\n# BEGIN thunder-forge\nold\n# END thunder-forge\n"
    managed = "# BEGIN thunder-forge\nnew\n# END thunder-forge\n"
    out = upsert_managed_hosts_block(
        hosts_file_text=hosts,
        managed_block=managed,
        settings=inv_settings(),
    )
    assert "old" not in out
    assert "new" in out


def inv_settings():
    return HostsSyncSettings(managed_block_start="# BEGIN thunder-forge", managed_block_end="# END thunder-forge")
