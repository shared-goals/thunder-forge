"""Remote cache command builders for artifact workflows."""

from __future__ import annotations

import base64
import json
import shlex

from thunder_forge.cluster.artifacts import build_artifact_identity
from thunder_forge.cluster.config import Node


def cache_hub_setup_command() -> str:
    return (
        "set -euo pipefail; "
        'CACHE_DIR="${TF_CACHE_OMLX_MODELS_DIR:-$HOME/.omlx/models}"; '
        '/bin/mkdir -p "$CACHE_DIR"; '
        'echo "cache: oMLX model hub ready at $CACHE_DIR"'
    )


def remote_artifact_download_command(*, repo_id: str, model_dir_name: str, timeout: int) -> str:
    payload_b64 = base64.b64encode(
        json.dumps(
            {
                "repo_id": repo_id,
                "model_dir_name": model_dir_name,
                "timeout": timeout,
            }
        ).encode("utf-8")
    ).decode("ascii")
    script = """python3 -u - <<'PY'
import base64
import json
import os
import shutil
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
payload = json.loads(base64.b64decode(__PAYLOAD_B64__).decode())
repo_id = str(payload['repo_id'])
model_dir_name = str(payload['model_dir_name'])
timeout_seconds = int(payload['timeout'])
cache_root = os.environ.get('TF_CACHE_OMLX_MODELS_DIR') or os.path.expanduser('~/.omlx/models')
model_dir = os.path.join(cache_root, model_dir_name)
base_url = 'http://127.0.0.1:8020'
env = dict(os.environ)
env.pop('ALL_PROXY', None)
env.pop('all_proxy', None)
def _model_complete(path):
    if not os.path.isdir(path):
        return False
    if not os.path.isfile(os.path.join(path, 'config.json')):
        return False
    if os.path.exists(os.path.join(path, '.rsync-partial')):
        return False
    has_weights = False
    for root, _, files in os.walk(path):
        for name in files:
            if name.endswith('.incomplete'):
                return False
            if name.endswith('.safetensors') or name.endswith('.bin'):
                has_weights = True
    return has_weights
def _request(method, path, payload=None, opener=None):
    req = urllib.request.Request(base_url + path, method=method)
    req.add_header('Content-Type', 'application/json')
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
    client = opener if opener is not None else urllib.request
    return client.open(req, data=data, timeout=30)
def _health_ready():
    try:
        with urllib.request.urlopen(base_url + '/health', timeout=2) as response:
            return 200 <= response.status < 300
    except Exception:
        return False
def _resolve_omlx_bin():
    if env.get('OMLX_BIN'):
        candidate = os.path.expanduser(env['OMLX_BIN'])
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    candidate = shutil.which('omlx', path=env.get('PATH'))
    if candidate:
        return candidate
    shell = env.get('SHELL') or '/bin/zsh'
    try:
        probe = subprocess.run(
            [shell, '-lc', 'command -v omlx'],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            env=env,
        )
        resolved = (probe.stdout or '').strip()
        if probe.returncode == 0 and resolved:
            return resolved
    except Exception:
        pass
    tool_dirs = [
        os.path.expanduser('~/.local/bin'),
        os.path.expanduser('~/.cargo/bin'),
        os.path.expanduser('~/.local/share/uv/tools/omlx/bin'),
    ]
    for directory in tool_dirs:
        candidate = os.path.join(directory, 'omlx')
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None
if _model_complete(model_dir):
    print('download_status: already_ready')
    raise SystemExit(0)
downloader_api_key = env.get('TF_OMLX_DOWNLOADER_API_KEY') or env.get('OMLX_API_KEY')
server_proc = None
server_started = False
server_stderr = ''
try:
    if not _health_ready():
        downloader_api_key = secrets.token_urlsafe(24)
        omlx_bin = _resolve_omlx_bin()
        if not omlx_bin:
            raise RuntimeError(
                'oMLX CLI is not on PATH for non-interactive shell; '
                'set OMLX_BIN or ensure command -v omlx works in login shell'
            )
        serve_variants = [
            [
                omlx_bin, 'serve', '--host', '127.0.0.1', '--port', '8020',
                '--model-dir', cache_root, '--max-model-memory', 'disabled',
                '--api-key', downloader_api_key,
            ],
            [
                omlx_bin, 'serve', '--host', '127.0.0.1', '--port', '8020',
                '--model-dir', cache_root, '--api-key', downloader_api_key,
            ],
        ]
        for idx, serve_args in enumerate(serve_variants):
            server_proc = subprocess.Popen(
                serve_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            server_started = True
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if server_proc.poll() is not None:
                    _, stderr_text = server_proc.communicate()
                    server_stderr = (stderr_text or '').strip()
                    break
                if _health_ready():
                    break
                time.sleep(1)
            if _health_ready():
                break
            if server_proc.poll() is None:
                server_proc.terminate()
                try:
                    _, stderr_text = server_proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    server_proc.kill()
                    _, stderr_text = server_proc.communicate(timeout=10)
                server_stderr = (stderr_text or '').strip()
            if idx == len(serve_variants) - 1:
                detail = (
                    f': {server_stderr}' if server_stderr else ''
                )
                raise RuntimeError(
                    f'oMLX downloader server exited with code {server_proc.returncode}{detail}'
                )
        else:
            raise TimeoutError('oMLX downloader server did not become ready: ' + base_url)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )
    if downloader_api_key:
        with _request(
            'POST',
            '/admin/api/login',
            {'api_key': downloader_api_key, 'remember': False},
            opener=opener,
        ) as response:
            if response.status >= 400:
                raise RuntimeError('admin login failed')
    hf_token = env.get('HF_TOKEN', '')
    try:
        with _request(
            'POST',
            '/admin/api/hf/download',
            {'repo_id': repo_id, 'hf_token': hf_token},
            opener=opener,
        ) as response:
            task = json.loads(response.read().decode('utf-8')).get('task')
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        if exc.code == 400 and 'already in progress' in body:
            with _request('GET', '/admin/api/hf/tasks', opener=opener) as response:
                tasks = json.loads(response.read().decode('utf-8')).get('tasks', [])
            task = next(
                (
                    item
                    for item in tasks
                    if item.get('repo_id') == repo_id
                    and item.get('status') in {'pending', 'downloading'}
                ),
                None,
            )
            if task is None:
                raise RuntimeError('No active oMLX download task found for ' + repo_id)
        elif exc.code == 401:
            raise RuntimeError('oMLX downloader admin API requires authentication')
        else:
            raise RuntimeError(body or str(exc))
    if not isinstance(task, dict) or not task.get('task_id'):
        raise RuntimeError('oMLX downloader returned no task for ' + repo_id)
    task_id = task['task_id']
    deadline = time.monotonic() + timeout_seconds
    last_bucket = -1
    while time.monotonic() < deadline:
        with _request('GET', '/admin/api/hf/tasks', opener=opener) as response:
            tasks = json.loads(response.read().decode('utf-8')).get('tasks', [])
        current = next((item for item in tasks if item.get('task_id') == task_id), None)
        if current is None:
            time.sleep(2)
            continue
        status = str(current.get('status') or 'unknown')
        progress = float(current.get('progress') or 0.0)
        bucket = int(progress)
        if bucket != last_bucket:
            print(f'download_progress: {status} {progress:.1f}%')
            last_bucket = bucket
        if status == 'completed':
            print('download_status: completed')
            raise SystemExit(0)
        if status in {'failed', 'cancelled'}:
            error = current.get('error') or ('download ' + status)
            raise RuntimeError('oMLX download failed for ' + repo_id + ': ' + str(error))
        time.sleep(2)
    raise TimeoutError('Timed out waiting for oMLX download ' + repo_id)
finally:
    if server_started and server_proc is not None:
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait(timeout=10)
PY"""
    return script.replace("__PAYLOAD_B64__", repr(payload_b64))


def remote_cache_sync_command(
    *,
    repo_id: str,
    runtime_node: Node,
    node_home_dir: str,
    transport_host: str,
    ssh_host_key_alias: str | None,
) -> tuple[str, str, str]:
    identity = build_artifact_identity(repo_id)
    source_root = '${TF_CACHE_OMLX_MODELS_DIR:-$HOME/.omlx/models}'
    source_path = f"{source_root}/{identity.model_dir_name}/"
    remote_omlx_models_dir = f"{node_home_dir}/.omlx/models"
    remote_model_parent_dir = f"{remote_omlx_models_dir}/{identity.namespace}"
    destination = f"{runtime_node.user}@{transport_host}:{remote_omlx_models_dir}/{identity.model_dir_name}/"

    ssh_options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if ssh_host_key_alias:
        ssh_options.extend(["-o", f"HostKeyAlias={ssh_host_key_alias}"])

    ssh_mkdir_cmd = " ".join(
        shlex.quote(arg)
        for arg in [
            "ssh",
            *ssh_options,
            f"{runtime_node.user}@{transport_host}",
            "mkdir",
            "-p",
            remote_model_parent_dir,
        ]
    )
    rsync_cmd = " ".join(
        shlex.quote(arg)
        for arg in [
            "rsync",
            "-a",
            "--progress",
            "--partial-dir=.rsync-partial",
            "--exclude",
            ".cache/",
            "-e",
            "ssh " + " ".join(shlex.quote(option) for option in ssh_options),
            "__TF_SOURCE_PATH__",
            destination,
        ]
    ).replace(shlex.quote("__TF_SOURCE_PATH__"), '"$SOURCE_PATH"')

    command = (
        "set -euo pipefail; "
        'CACHE_ROOT="${TF_CACHE_OMLX_MODELS_DIR:-$HOME/.omlx/models}"; '
        f'SOURCE_PATH="$CACHE_ROOT/{identity.model_dir_name}/"; '
        'if [[ ! -d "$SOURCE_PATH" ]]; then '
        'echo "missing cache oMLX model dir: $SOURCE_PATH" >&2; '
        "exit 2; "
        "fi; "
        'test -f "$SOURCE_PATH/config.json" || { '
        'echo "incomplete cache oMLX model dir: missing config.json: $SOURCE_PATH" >&2; '
        "exit 2; "
        "}; "
        'test -z "$(find "$SOURCE_PATH" -name \'*.incomplete\' -print -quit)" || { '
        'echo "incomplete cache oMLX model dir: partial artifact present: $SOURCE_PATH" >&2; '
        "exit 2; "
        "}; "
        'test ! -e "$SOURCE_PATH/.rsync-partial" || { '
        'echo "incomplete cache oMLX model dir: rsync partial dir present: $SOURCE_PATH" >&2; '
        "exit 2; "
        "}; "
        'test -n "$(find "$SOURCE_PATH" \\( -name \'*.safetensors\' -o -name \'*.bin\' \\) -type f -print -quit)" || { '
        'echo "incomplete cache oMLX model dir: no weight files found: $SOURCE_PATH" >&2; '
        "exit 2; "
        "}; "
        f"{ssh_mkdir_cmd}; "
        f"{rsync_cmd}"
    )
    return source_path, destination, command


def remote_transport_plan_probe_command(*, payload_b64: str) -> str:
    script = """python3 - <<'PY'
import base64
import ipaddress
import json
import platform
import re
import subprocess
payload = json.loads(base64.b64decode(__PAYLOAD_B64__).decode())
requested_transport = payload['requested_transport']
management_host = payload['management_host']
node_user = payload['node_user']
fabric_host = payload['fabric_host']
timeout = payload['timeout']
plan = {
    'requested_transport': requested_transport,
    'management_host': management_host,
    'transport_host': management_host,
    'resolved_transport_host': management_host,
    'fabric_fallback': '',
    'error': '',
}
if requested_transport not in {'auto', 'fabric', 'management'}:
    plan['error'] = '--transport must be one of: auto, fabric, management'
elif requested_transport == 'management':
    pass
elif not fabric_host:
    if requested_transport == 'fabric':
        plan['error'] = 'fabric probe disabled for node'
else:
    def _run(cmd, extra_timeout=0):
        try:
            return subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout + extra_timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
    def _extract_thunderbolt_devices(text):
        devices = set()
        for block in re.split(r'\\n\\s*\\n', text):
            if 'Thunderbolt' not in block:
                continue
            match = re.search(r'^Device:\\s*(\\S+)\\s*$', block, flags=re.MULTILINE)
            if match:
                devices.add(match.group(1))
        return devices
    def _acceptable(address):
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
        return parsed.version == 4 and parsed.is_link_local
    def _extract_link_local_ipv4(text, allowed_interfaces):
        addresses = []
        current_interface = ''
        for line in text.splitlines():
            interface_match = re.match(r'^([A-Za-z0-9._-]+):', line)
            if interface_match:
                current_interface = interface_match.group(1)
            if current_interface not in allowed_interfaces:
                continue
            match = re.search(r'\\binet\\s+(169\\.254\\.\\d{1,3}\\.\\d{1,3})\\b', line)
            if match:
                address = match.group(1)
                if _acceptable(address) and address not in addresses:
                    addresses.append(address)
        return addresses
    def _route_uses_allowed_interface(address, allowed_interfaces):
        result = _run(['route', '-n', 'get', address])
        if result is None or result.returncode != 0:
            return False
        match = re.search(r'^\\s*interface:\\s*(\\S+)\\s*$', result.stdout, flags=re.MULTILINE)
        return bool(match and match.group(1) in allowed_interfaces)
    def _ssh_hostname_check(address):
        result = _run([
            'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
            '-o', f'HostKeyAlias={management_host}',
            f'{node_user}@{address}', 'hostname'
        ], extra_timeout=8)
        return result is not None and result.returncode == 0
    resolved = None
    if platform.system() == 'Darwin':
        local_inventory = _run(['networksetup', '-listallhardwareports'])
        if local_inventory is not None and local_inventory.returncode == 0:
            local_devices = _extract_thunderbolt_devices(local_inventory.stdout)
            if local_devices:
                remote_inventory = _run([
                    'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                    f'{node_user}@{management_host}',
                    (
                        "networksetup -listallhardwareports 2>/dev/null; "
                        "printf '\\n__TF_IFCONFIG__\\n'; ifconfig"
                    ),
                ], extra_timeout=8)
                if remote_inventory is not None and remote_inventory.returncode == 0:
                    inventory, _, ifconfig_text = remote_inventory.stdout.partition('__TF_IFCONFIG__')
                    remote_devices = _extract_thunderbolt_devices(inventory)
                    for address in _extract_link_local_ipv4(
                        ifconfig_text, remote_devices
                    ):
                        if _route_uses_allowed_interface(
                            address, local_devices
                        ) and _ssh_hostname_check(address):
                            resolved = address
                            break
    if resolved is not None:
        plan['transport_host'] = resolved
        plan['resolved_transport_host'] = resolved
    elif requested_transport == 'fabric':
        plan['error'] = 'no reachable fabric address discovered'
    else:
        plan['fabric_fallback'] = 'dynamic probe unresolved'
print('__TF_TRANSPORT_PLAN__' + json.dumps(plan, sort_keys=True))
PY"""
    return script.replace("__PAYLOAD_B64__", repr(payload_b64))
