"""CLI tests for the usage check threshold command."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from textwrap import dedent

import pytest
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
    assert "requests=5 failures=1 rate=20.0% threshold=30.0%" in result.stdout
    assert "result=ok" in result.stdout


def test_usage_check_exits_one_when_failure_rate_exceeds_threshold(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(
        tmp_path,
        monkeypatch,
        [_record(200, minute) for minute in range(1, 9)] + [_record(502, 9), _record(502, 10)],
    )

    result = runner.invoke(app, ["usage", "check"])

    assert result.exit_code == 1
    assert "requests=10 failures=2 rate=20.0% threshold=5.0%" in result.stdout
    assert "result=breach" in result.stdout


def test_usage_check_reports_insufficient_data_below_min_requests(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [_record(200, 1), _record(200, 2)])

    result = runner.invoke(app, ["usage", "check", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["requests"] == 2
    assert payload["failure_rate"] == 0.0
    assert payload["insufficient_data"] is True
    assert payload["breached"] is False


def test_usage_check_breaches_when_every_request_in_a_small_window_failed(
    tmp_path: Path, monkeypatch
) -> None:
    """A quiet window where everything failed is an outage, not insufficient data."""
    _repo_with_access_log(tmp_path, monkeypatch, [_record(502, 1), _record(502, 2)])

    result = runner.invoke(app, ["usage", "check", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["requests"] == 2
    assert payload["failure_rate"] == 1.0
    assert payload["insufficient_data"] is False
    assert payload["breached"] is True


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
    assert "failures=0" in default_result.stdout
    assert client_error_result.exit_code == 1
    assert "failures=1 rate=16.7%" in client_error_result.stdout


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
        "newest_record_at",
        "stale",
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


def test_usage_check_rejects_a_window_too_large_for_a_timestamp(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [_record(200, 1)])

    result = runner.invoke(app, ["usage", "check", "--window", "999999999999999h"])

    assert result.exit_code == 2
    assert "is too large" in result.stderr


def test_usage_check_counts_a_stream_abort_and_ignores_a_client_abort(tmp_path: Path, monkeypatch) -> None:
    """A 200 row with a cluster error_kind is a failure; a client hang-up is not."""
    records = [
        {**_record(200, 1), "error_kind": "stream_aborted"},
        {**_record(200, 2), "error_kind": "client_aborted"},
        {**_record(200, 3), "error_kind": ""},
        _record(200, 4),
    ]
    _repo_with_access_log(tmp_path, monkeypatch, records)

    result = runner.invoke(app, ["usage", "check", "--window", "1h", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["requests"] == 4
    assert payload["failures"] == 1


@pytest.mark.parametrize(("window", "seconds"), [("30s", 30), ("15m", 900), ("2h", 7200)])
def test_usage_check_window_units(tmp_path: Path, monkeypatch, window: str, seconds: int) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [_record(200, 1)])

    result = runner.invoke(app, ["usage", "check", "--window", window, "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["window_seconds"] == seconds


def test_usage_check_rejects_a_zero_window(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [_record(200, 1)])

    result = runner.invoke(app, ["usage", "check", "--window", "0s"])

    assert result.exit_code == 2
    assert "invalid window '0s'" in result.stderr


def test_usage_check_reads_the_log_given_on_the_command_line(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [])
    other = tmp_path / "copied.jsonl"
    other.write_text(json.dumps(_record(502, 1)) + "\n" * 1)

    result = runner.invoke(
        app, ["usage", "check", "--access-log", str(other), "--window", "1h", "--min-requests", "1", "--json"]
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["requests"] == 1
    assert payload["log_path"] == str(other)


def test_usage_check_reports_silence_when_the_log_stops_growing(tmp_path: Path, monkeypatch) -> None:
    """A dead edge writes nothing; with --max-staleness that is a breach, not a healthy zero."""
    _repo_with_access_log(tmp_path, monkeypatch, [_record(200, 180)])

    result = runner.invoke(app, ["usage", "check", "--window", "15m", "--max-staleness", "30m"])

    assert result.exit_code == 1
    assert "result=stale_log" in result.stdout


def test_usage_check_reports_an_empty_log_as_insufficient(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [])

    result = runner.invoke(app, ["usage", "check", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["requests"] == 0
    assert payload["insufficient_data"] is True
    assert payload["newest_record_at"] is None


def test_usage_check_rejects_a_nan_failure_rate(tmp_path: Path, monkeypatch) -> None:
    _repo_with_access_log(tmp_path, monkeypatch, [_record(200, 1)])

    result = runner.invoke(app, ["usage", "check", "--max-failure-rate", "nan"])

    assert result.exit_code == 2
    assert "ratio between 0 and 1" in result.stderr


def test_usage_check_rejects_a_staleness_window_too_large_for_a_timestamp(tmp_path: Path, monkeypatch) -> None:
    """An unrepresentable window must exit 2, never 1 — exit 1 reads as a real breach in cron."""
    _repo_with_access_log(tmp_path, monkeypatch, [_record(200, 1)])

    result = runner.invoke(app, ["usage", "check", "--max-staleness", "99999999999999999999h"])

    assert result.exit_code == 2
    assert "too large" in result.stderr


def test_usage_check_reads_a_log_without_any_config_beside_it(tmp_path: Path, monkeypatch) -> None:
    """Checking a copy taken off the host must not need a tfconfig.yaml."""
    import thunder_forge.cli as cli_module

    monkeypatch.setattr(cli_module, "_load_config", lambda: (_ for _ in ()).throw(AssertionError("config loaded")))
    log = tmp_path / "copied.jsonl"
    log.write_text(json.dumps(_record(502, 1)) + "\n")

    result = runner.invoke(
        app, ["usage", "check", "--access-log", str(log), "--min-requests", "1", "--json"]
    )

    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["log_path"] == str(log)


def test_usage_check_survives_a_log_cut_mid_character(tmp_path: Path, monkeypatch) -> None:
    """A copy taken off the host can be truncated mid-UTF-8; that is a skipped line, not an exit 1."""
    log = tmp_path / "copied.jsonl"
    log.write_bytes(b'{"timestamp": "2026-11-27T10:00:00+00:00", "status_code": 200}\n\xff\xfe\n')

    result = runner.invoke(
        app, ["usage", "check", "--access-log", str(log), "--window", "9999h", "--json"]
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["requests"] == 1
