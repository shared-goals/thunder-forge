"""CLI tests for the usage check threshold command."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from thunder_forge.cli import app

runner = CliRunner()


def _record(status_code: int, minutes_ago: float) -> dict[str, object]:
    timestamp = (datetime.now().astimezone() - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "timestamp": timestamp,
        "client_id": "alice",
        "model": "coder",
        "latency_ms": 100,
        "olla_endpoint": "msm1-omlx-live",
        "status_code": status_code,
    }


def _repo_with_access_log(tmp_path: Path, monkeypatch, records: list[dict[str, object]]) -> Path:
    repo = tmp_path
    (repo / "tfconfig.yaml").write_text(
        dedent(
            """\
            services:
              edge:
                access_log: logs/tf-edge-access.jsonl
            models: {}
            nodes: {}
        """
        )
    )
    (repo / "logs").mkdir()
    (repo / "logs" / "tf-edge-access.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )

    import thunder_forge.cluster.config as config_module

    monkeypatch.setattr(config_module, "find_repo_root", lambda: repo)
    return repo


def test_usage_check_reports_healthy_window(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(
        tmp_path,
        monkeypatch,
        [_record(200, 1), _record(200, 2), _record(200, 3), _record(200, 4), _record(500, 5)],
    )

    result = runner.invoke(app, ["usage", "check", "--max-failure-rate", "0.3"])

    assert result.exit_code == 0
    assert (
        "usage check: window=15m requests=5 failures_5xx=1 rate=20.0% threshold=30.0% result=ok" in result.stdout
    )


def test_usage_check_exits_one_when_failure_rate_exceeds_threshold(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(
        tmp_path,
        monkeypatch,
        [_record(200, minute) for minute in range(1, 9)] + [_record(502, 9), _record(502, 10)],
    )

    result = runner.invoke(app, ["usage", "check"])

    assert result.exit_code == 1
    assert "requests=10 failures_5xx=2 rate=20.0% threshold=5.0% result=breach" in result.stdout


def test_usage_check_reports_insufficient_data_below_min_requests(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [_record(502, 1), _record(502, 2)])

    result = runner.invoke(app, ["usage", "check", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["requests"] == 2
    assert payload["failure_rate"] == 1.0
    assert payload["insufficient_data"] is True
    assert payload["breached"] is False


def test_usage_check_ignores_records_outside_the_window(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [_record(200, 1), _record(502, 60)])

    result = runner.invoke(app, ["usage", "check", "--window", "15m", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["window_seconds"] == 900
    assert payload["requests"] == 1
    assert payload["failures"] == 0
    assert payload["statuses"] == {"200": 1}


def test_usage_check_include_client_errors_counts_4xx(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(
        tmp_path,
        monkeypatch,
        [_record(200, minute) for minute in range(1, 6)] + [_record(401, 6)],
    )

    default_result = runner.invoke(app, ["usage", "check"])
    client_error_result = runner.invoke(app, ["usage", "check", "--include-client-errors"])

    assert default_result.exit_code == 0
    assert "failures_5xx=0" in default_result.stdout
    assert client_error_result.exit_code == 1
    assert "failures_4xx_5xx=1 rate=16.7%" in client_error_result.stdout


def test_usage_check_json_payload_shape(tmp_path: Path, monkeypatch) -> None:
    repo = _repo_with_access_log(tmp_path, monkeypatch, [_record(200, 1), _record(502, 2)])

    result = runner.invoke(app, ["usage", "check", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "window_seconds",
        "requests",
        "failures",
        "failure_rate",
        "threshold",
        "min_requests",
        "breached",
        "insufficient_data",
        "statuses",
        "log_path",
        "checked_at",
    }
    assert payload["threshold"] == 0.05
    assert payload["min_requests"] == 5
    assert payload["log_path"] == str(repo / "logs" / "tf-edge-access.jsonl")
    assert payload["statuses"] == {"200": 1, "502": 1}


def test_usage_check_missing_log_is_an_error(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [])
    (tmp_path / "logs" / "tf-edge-access.jsonl").unlink()

    result = runner.invoke(app, ["usage", "check"])

    assert result.exit_code == 2
    assert "access log not found" in result.stderr


def test_usage_check_rejects_invalid_window(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [_record(200, 1)])

    result = runner.invoke(app, ["usage", "check", "--window", "15"])

    assert result.exit_code == 2
    assert "invalid window '15'" in result.stderr
