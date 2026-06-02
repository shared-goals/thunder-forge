"""Cluster prepare helpers for the pre-MVP operator flow."""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from thunder_forge.cluster.artifacts import cache_omlx_models_dir_from_env
from thunder_forge.cluster.config import (
    DEFAULT_OLLA_VERSION,
    ClusterConfig,
    generate_olla_config,
    generated_olla_config_path,
)

Progress = Callable[[str], None]
LATEST_OLLA_RELEASE_API = "https://api.github.com/repos/thushan/olla/releases/latest"


@dataclass(frozen=True)
class OllaBinaryResult:
    version: str
    asset: str
    binary_path: Path
    status: str
    expected_sha256: str


def ensure_olla_binary(
    *,
    version: str,
    os_name: str,
    arch: str,
    bin_dir: Path,
    upgrade: bool = False,
    release_base: str | None = None,
    timeout: int = 60,
    progress: Progress | None = None,
) -> OllaBinaryResult:
    """Install or upgrade Olla binary for the requested version (or latest when unpinned)."""
    resolved_version = _resolve_olla_version(version, timeout=timeout, progress=progress)
    asset_prefix = f"olla_{resolved_version}_{os_name}_{arch}"
    base_url = release_base or f"https://github.com/thushan/olla/releases/download/{resolved_version}"
    checksums_url = f"{base_url}/checksums.txt"
    bin_dir.mkdir(parents=True, exist_ok=True)
    binary_path = bin_dir / "olla"
    sidecar_path = bin_dir / ".olla.sha256"

    checksums = _download_text(checksums_url, timeout=timeout)
    asset, expected_sha256 = _resolve_asset_and_checksum(checksums, asset_prefix)
    if progress:
        progress(f"olla: checking {resolved_version} ({asset})")

    binary_exists = binary_path.exists() and bool(binary_path.stat().st_mode & 0o111)
    sidecar_matches = sidecar_path.exists() and sidecar_path.read_text().strip() == expected_sha256

    if binary_exists and sidecar_matches and not upgrade:
        if progress:
            progress(f"olla: already current at {binary_path}")
        return OllaBinaryResult(
            version=resolved_version,
            asset=asset,
            binary_path=binary_path,
            status="current",
            expected_sha256=expected_sha256,
        )

    if binary_exists and sidecar_matches and upgrade and progress:
        progress(f"olla: upgrading {resolved_version} ({asset})")

    if progress:
        progress(f"olla: downloading {asset}")
    archive_bytes = _download_bytes(f"{base_url}/{asset}", timeout=timeout, progress=progress)
    actual_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        msg = f"checksum mismatch for {asset}: expected {expected_sha256}, got {actual_sha256}"
        raise ValueError(msg)

    with tempfile.TemporaryDirectory(prefix="thunder-forge-olla-") as temp_dir:
        temp_path = Path(temp_dir)
        archive_path = temp_path / asset
        archive_path.write_bytes(archive_bytes)
        _extract_archive(archive_path, temp_path)
        extracted_binary = _find_extracted_binary(temp_path)
        if not extracted_binary.exists():
            msg = f"Olla archive did not contain executable 'olla': {asset}"
            raise ValueError(msg)
        installed_path = bin_dir / "olla.new"
        shutil.copy2(extracted_binary, installed_path)
        installed_path.chmod(0o755)
        installed_path.replace(binary_path)
    sidecar_path.write_text(f"{expected_sha256}\n")
    status = "upgraded" if binary_exists else "installed"
    if progress:
        progress(f"olla: {status} {binary_path}")
    return OllaBinaryResult(
        version=resolved_version,
        asset=asset,
        binary_path=binary_path,
        status=status,
        expected_sha256=expected_sha256,
    )


def _resolve_olla_version(version: str, *, timeout: int, progress: Progress | None = None) -> str:
    requested = version.strip()
    if requested and requested != "latest":
        return requested

    try:
        payload = json.loads(_download_text(LATEST_OLLA_RELEASE_API, timeout=timeout))
        latest = str(payload.get("tag_name", "")).strip()
        if latest:
            if progress:
                progress(f"olla: resolved latest release {latest}")
            return latest
    except (ValueError, TypeError, OSError, RuntimeError, TimeoutError):
        pass

    if progress:
        progress(f"olla: latest release lookup failed, using {DEFAULT_OLLA_VERSION}")
    return DEFAULT_OLLA_VERSION


def write_generated_olla_config(config: ClusterConfig, *, repo_root: Path, port: int | None = None) -> Path:
    config_path = generated_olla_config_path(repo_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(generate_olla_config(config, port=port))
    return config_path


def ensure_cache_hub_dir(*, progress: Progress | None = None) -> Path:
    cache_dir = Path(cache_omlx_models_dir_from_env()).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"cache: oMLX model hub ready at {cache_dir}")
    return cache_dir


def _download_text(url: str, *, timeout: int) -> str:
    return _download_bytes(url, timeout=timeout).decode()


def _download_bytes(url: str, *, timeout: int, progress: Progress | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "thunder-forge"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        total_raw = response.headers.get("Content-Length", "0")
        total = int(total_raw) if total_raw.isdigit() else 0
        chunks: list[bytes] = []
        downloaded = 0
        last_bucket = -1
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            downloaded += len(chunk)
            if progress and total > 0:
                bucket = int((downloaded / total) * 20)
                if bucket != last_bucket:
                    last_bucket = bucket
                    progress(f"download: {downloaded / total:.0%} ({downloaded // 1024 // 1024} MiB)")
        return b"".join(chunks)


def _checksum_for_asset(checksums: str, asset: str) -> str:
    for line in checksums.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        name = fields[-1].removeprefix("./")
        if name == asset:
            return fields[0]
    msg = f"checksum entry not found for {asset}"
    raise ValueError(msg)


def _resolve_asset_and_checksum(checksums: str, asset_prefix: str) -> tuple[str, str]:
    candidates = [f"{asset_prefix}.zip", f"{asset_prefix}.tar.gz"]
    for asset in candidates:
        try:
            return asset, _checksum_for_asset(checksums, asset)
        except ValueError:
            continue
    msg = f"checksum entry not found for any of: {', '.join(candidates)}"
    raise ValueError(msg)


def _extract_archive(archive_path: Path, destination: Path) -> None:
    if archive_path.name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(destination)
        return
    if archive_path.name.endswith(".tar.gz"):
        with tarfile.open(archive_path, mode="r:gz") as archive:
            archive.extractall(path=destination, filter="data")
        return
    msg = f"unsupported Olla archive format: {archive_path.name}"
    raise ValueError(msg)


def _find_extracted_binary(extracted_root: Path) -> Path:
    direct = extracted_root / "olla"
    if direct.is_file():
        return direct
    for candidate in extracted_root.rglob("olla"):
        if candidate.is_file():
            return candidate
    msg = "Olla archive did not contain executable 'olla'"
    raise ValueError(msg)
