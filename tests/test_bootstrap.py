"""Tests for Olla bootstrap artifact handling."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

from thunder_forge.cluster.bootstrap import ensure_olla_binary


def _zip_archive_with_olla(payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("olla", payload)
    return buffer.getvalue()


def _tar_gz_archive_with_olla(payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name="olla")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_ensure_olla_binary_selects_linux_tar_gz_asset(tmp_path: Path, monkeypatch) -> None:
    archive_bytes = _tar_gz_archive_with_olla(b"#!/bin/sh\necho linux\n")
    checksum = hashlib.sha256(archive_bytes).hexdigest()
    checksums = (
        f"{checksum}  olla_v0.0.27_linux_arm64.tar.gz\n"
        "deadbeef  olla_v0.0.27_macos_arm64.zip\n"
    )

    monkeypatch.setattr("thunder_forge.cluster.bootstrap._download_text", lambda *_args, **_kwargs: checksums)

    def fake_download_bytes(url: str, *, timeout: int, progress=None) -> bytes:
        assert url.endswith("olla_v0.0.27_linux_arm64.tar.gz")
        return archive_bytes

    monkeypatch.setattr("thunder_forge.cluster.bootstrap._download_bytes", fake_download_bytes)

    result = ensure_olla_binary(
        version="v0.0.27",
        os_name="linux",
        arch="arm64",
        bin_dir=tmp_path,
    )

    assert result.asset == "olla_v0.0.27_linux_arm64.tar.gz"
    assert result.status == "installed"
    assert result.binary_path.exists()
    assert result.binary_path.read_bytes() == b"#!/bin/sh\necho linux\n"


def test_ensure_olla_binary_selects_macos_zip_asset(tmp_path: Path, monkeypatch) -> None:
    archive_bytes = _zip_archive_with_olla(b"#!/bin/sh\necho macos\n")
    checksum = hashlib.sha256(archive_bytes).hexdigest()
    checksums = f"{checksum}  olla_v0.0.27_macos_arm64.zip\n"

    monkeypatch.setattr("thunder_forge.cluster.bootstrap._download_text", lambda *_args, **_kwargs: checksums)

    def fake_download_bytes(url: str, *, timeout: int, progress=None) -> bytes:
        assert url.endswith("olla_v0.0.27_macos_arm64.zip")
        return archive_bytes

    monkeypatch.setattr("thunder_forge.cluster.bootstrap._download_bytes", fake_download_bytes)

    result = ensure_olla_binary(
        version="v0.0.27",
        os_name="macos",
        arch="arm64",
        bin_dir=tmp_path,
    )

    assert result.asset == "olla_v0.0.27_macos_arm64.zip"
    assert result.status == "installed"
    assert result.binary_path.exists()
    assert result.binary_path.read_bytes() == b"#!/bin/sh\necho macos\n"


def test_ensure_olla_binary_upgrade_reinstalls_even_when_current(tmp_path: Path, monkeypatch) -> None:
    archive_bytes = _zip_archive_with_olla(b"#!/bin/sh\necho upgrade\n")
    checksum = hashlib.sha256(archive_bytes).hexdigest()
    checksums = f"{checksum}  olla_v0.0.27_macos_arm64.zip\n"
    calls: list[str] = []

    monkeypatch.setattr("thunder_forge.cluster.bootstrap._download_text", lambda *_args, **_kwargs: checksums)

    def fake_download_bytes(url: str, *, timeout: int, progress=None) -> bytes:
        calls.append(url)
        return archive_bytes

    monkeypatch.setattr("thunder_forge.cluster.bootstrap._download_bytes", fake_download_bytes)

    first = ensure_olla_binary(
        version="v0.0.27",
        os_name="macos",
        arch="arm64",
        bin_dir=tmp_path,
    )
    second = ensure_olla_binary(
        version="v0.0.27",
        os_name="macos",
        arch="arm64",
        bin_dir=tmp_path,
        upgrade=True,
    )

    assert first.status == "installed"
    assert second.status == "upgraded"
    assert len(calls) == 2


def test_ensure_olla_binary_latest_version_resolves_and_installs(tmp_path: Path, monkeypatch) -> None:
    archive_bytes = _zip_archive_with_olla(b"#!/bin/sh\necho latest\n")
    checksum = hashlib.sha256(archive_bytes).hexdigest()

    def fake_download_text(url: str, *, timeout: int) -> str:
        if url.endswith("/releases/latest"):
            return '{"tag_name":"v9.9.9"}'
        if url.endswith("/v9.9.9/checksums.txt"):
            return f"{checksum}  olla_v9.9.9_macos_arm64.zip\n"
        raise AssertionError(url)

    def fake_download_bytes(url: str, *, timeout: int, progress=None) -> bytes:
        assert url.endswith("/v9.9.9/olla_v9.9.9_macos_arm64.zip")
        return archive_bytes

    monkeypatch.setattr("thunder_forge.cluster.bootstrap._download_text", fake_download_text)
    monkeypatch.setattr("thunder_forge.cluster.bootstrap._download_bytes", fake_download_bytes)

    result = ensure_olla_binary(
        version="latest",
        os_name="macos",
        arch="arm64",
        bin_dir=tmp_path,
    )

    assert result.version == "v9.9.9"
    assert result.asset == "olla_v9.9.9_macos_arm64.zip"
    assert result.status == "installed"


def test_ensure_olla_binary_latest_version_falls_back_to_default_when_lookup_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_bytes = _zip_archive_with_olla(b"#!/bin/sh\necho fallback\n")
    checksum = hashlib.sha256(archive_bytes).hexdigest()

    def fake_download_text(url: str, *, timeout: int) -> str:
        if url.endswith("/releases/latest"):
            raise OSError("offline")
        if url.endswith("/v0.0.27/checksums.txt"):
            return f"{checksum}  olla_v0.0.27_macos_arm64.zip\n"
        raise AssertionError(url)

    def fake_download_bytes(url: str, *, timeout: int, progress=None) -> bytes:
        assert url.endswith("/v0.0.27/olla_v0.0.27_macos_arm64.zip")
        return archive_bytes

    monkeypatch.setattr("thunder_forge.cluster.bootstrap._download_text", fake_download_text)
    monkeypatch.setattr("thunder_forge.cluster.bootstrap._download_bytes", fake_download_bytes)

    result = ensure_olla_binary(
        version="latest",
        os_name="macos",
        arch="arm64",
        bin_dir=tmp_path,
    )

    assert result.version == "v0.0.27"
    assert result.asset == "olla_v0.0.27_macos_arm64.zip"
    assert result.status == "installed"
