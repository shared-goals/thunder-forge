"""Deployment: plist generation, SSH deploy, launchctl management."""

from __future__ import annotations

import atexit
import os
import threading
import time as time_mod
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from thunder_forge.cluster.config import Assignment, ClusterConfig, Model, Node, NodeRole, ServingMode
from thunder_forge.cluster.ssh import scp_content, ssh_run

LOCK_FILE = "/tmp/thunder-forge-deploy.lock"
HEARTBEAT_INTERVAL = 30


def parse_lock_file(content: str | None) -> dict | None:
    """Parse lock file content. Returns dict with pid/heartbeat or None."""
    if not content or not content.strip():
        return None
    result = {}
    for line in content.strip().split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lower()
            try:
                result[key] = int(val.strip())
            except ValueError:
                result[key] = val.strip()
    if "pid" not in result:
        return None
    return result


def format_lock_file(pid: int) -> str:
    """Format lock file content with PID and current timestamp."""
    return f"PID:{pid}\nHEARTBEAT:{int(time_mod.time())}"


def _acquire_lock() -> bool:
    """Acquire the gateway deploy lock. Returns True if acquired."""
    lock_path = Path(LOCK_FILE)
    if lock_path.exists():
        content = lock_path.read_text()
        lock = parse_lock_file(content)
        if lock:
            pid = lock.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                except OSError:
                    pass  # PID dead, stale lock
                else:
                    heartbeat = lock.get("heartbeat", 0)
                    if isinstance(heartbeat, int) and time_mod.time() - heartbeat < 300:
                        return False
    lock_path.write_text(format_lock_file(os.getpid()))
    return True


def _release_lock() -> None:
    """Release the gateway deploy lock."""
    lock_path = Path(LOCK_FILE)
    if lock_path.exists():
        content = lock_path.read_text()
        lock = parse_lock_file(content)
        if lock and lock.get("pid") == os.getpid():
            lock_path.unlink(missing_ok=True)


def _heartbeat_loop(stop_event: threading.Event) -> None:
    """Update lock file heartbeat periodically."""
    while not stop_event.is_set():
        lock_path = Path(LOCK_FILE)
        if lock_path.exists():
            lock_path.write_text(format_lock_file(os.getpid()))
        stop_event.wait(HEARTBEAT_INTERVAL)


def _require_resolved(node: Node, node_name: str) -> None:
    """Raise if resolved fields are missing (pre-flight not run)."""
    if node.home_dir is None:
        msg = f"{node_name}: node.home_dir is None — run pre-flight first (remove --skip-preflight)"
        raise ValueError(msg)


def _build_chat_args(model: Model, slot: Assignment, home: str) -> list[str]:
    """Build ProgramArguments for mlx_lm.server (chat/completion models)."""
    server_path = f"{home}/.local/bin/mlx_lm.server"
    args = [server_path, "--model", model.source.repo, "--port", str(slot.port), "--host", "0.0.0.0"]

    if model.enable_thinking is not None:
        import json

        args.extend(["--chat-template-args", json.dumps({"enable_thinking": model.enable_thinking})])

    if model.server_args:
        sa = model.server_args
        for flag, value in [
            ("--decode-concurrency", sa.decode_concurrency),
            ("--prompt-concurrency", sa.prompt_concurrency),
            ("--prefill-step-size", sa.prefill_step_size),
            ("--prompt-cache-size", sa.prompt_cache_size),
            ("--prompt-cache-bytes", sa.prompt_cache_bytes),
            ("--max-tokens", sa.max_tokens),
            ("--temp", sa.temp),
            ("--top-p", sa.top_p),
            ("--top-k", sa.top_k),
            ("--min-p", sa.min_p),
            ("--draft-model", sa.draft_model),
            ("--num-draft-tokens", sa.num_draft_tokens),
        ]:
            if value is not None:
                args.extend([flag, str(value)])

    if model.extra_args:
        args.extend(model.extra_args)
    return args


def _build_mlx_openai_server_args(model: Model, slot: Assignment, home: str, model_type: str) -> list[str]:
    """Build ProgramArguments for mlx-openai-server."""
    server_path = f"{home}/.local/bin/mlx-openai-server"
    args = [
        server_path,
        "launch",
        "--model-type",
        model_type,
        "--model-path",
        model.source.repo,
        "--port",
        str(slot.port),
        "--host",
        "0.0.0.0",
        "--no-log-file",
    ]
    if model.extra_args:
        args.extend(model.extra_args)
    return args


def _build_embedding_args(model: Model, slot: Assignment, home: str) -> list[str]:
    """Build ProgramArguments for mlx-openai-server embedding models."""
    return _build_mlx_openai_server_args(model, slot, home, "embeddings")


def _build_mlx_openai_lm_args(model: Model, slot: Assignment, home: str) -> list[str]:
    """Build ProgramArguments for mlx-openai-server LM models."""
    return _build_mlx_openai_server_args(model, slot, home, "lm")


def generate_plist(
    model: Model,
    slot: Assignment,
    node: Node,
) -> str:
    _require_resolved(node, f"port-{slot.port}")
    home = node.home_dir
    label = f"com.mlx-lm-{slot.port}"

    if model.serving == ServingMode.EMBEDDING:
        program_args = _build_embedding_args(model, slot, home)
    elif model.serving == ServingMode.MLX_OPENAI_SERVER:
        program_args = _build_mlx_openai_lm_args(model, slot, home)
    else:
        program_args = _build_chat_args(model, slot, home)

    path_parts = [f"{home}/.local/bin", "/usr/bin", "/bin"]
    if node.homebrew_prefix:
        path_parts.insert(1, f"{node.homebrew_prefix}/bin")

    env_vars = {
        "PATH": ":".join(path_parts),
        "HOME": home,
        "HF_HUB_OFFLINE": "1",
    }

    plist = ET.Element("plist", version="1.0")
    d = ET.SubElement(plist, "dict")

    def add_key_value(parent: ET.Element, key: str, value_elem: ET.Element) -> None:
        k = ET.SubElement(parent, "key")
        k.text = key
        parent.append(value_elem)

    def make_string(text: str) -> ET.Element:
        e = ET.Element("string")
        e.text = text
        return e

    def make_true() -> ET.Element:
        return ET.Element("true")

    def make_integer(val: int) -> ET.Element:
        e = ET.Element("integer")
        e.text = str(val)
        return e

    add_key_value(d, "Label", make_string(label))

    k = ET.SubElement(d, "key")
    k.text = "ProgramArguments"
    arr = ET.SubElement(d, "array")
    for arg in program_args:
        s = ET.SubElement(arr, "string")
        s.text = arg

    k = ET.SubElement(d, "key")
    k.text = "EnvironmentVariables"
    env_dict = ET.SubElement(d, "dict")
    for env_key, env_val in env_vars.items():
        ek = ET.SubElement(env_dict, "key")
        ek.text = env_key
        ev = ET.SubElement(env_dict, "string")
        ev.text = env_val

    add_key_value(d, "StandardOutPath", make_string(f"{home}/logs/mlx-lm-{slot.port}.log"))
    add_key_value(d, "StandardErrorPath", make_string(f"{home}/logs/mlx-lm-{slot.port}.err"))

    add_key_value(d, "RunAtLoad", make_true())
    add_key_value(d, "KeepAlive", make_true())
    add_key_value(d, "ThrottleInterval", make_integer(10))
    add_key_value(d, "ProcessType", make_string("Interactive"))

    ET.indent(plist, space="  ")
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    doctype = (
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    )
    body = ET.tostring(plist, encoding="unicode")
    return xml_declaration + doctype + body + "\n"


NEWSYSLOG_CONF = """\
# logfilename                             [owner:group] mode count size(KB) when  flags
{home}/logs/mlx-lm-*.log              {user}:staff     644  7     102400   *     CNJ
{home}/logs/mlx-lm-*.err              {user}:staff     644  7     102400   *     CNJ
"""


def _fetch_err_log(node: Node, port: int, lines: int = 30) -> str:
    """Fetch the last N lines of the mlx-lm error log for a port. Returns empty string on failure."""
    if node.home_dir is None:
        return ""
    result = ssh_run(
        node.user, node.ip,
        f"tail -{lines} {node.home_dir}/logs/mlx-lm-{port}.err 2>/dev/null",
        timeout=10,
        shell=node.shell,
    )
    return (result.stdout or "").strip()


def _kill_process_by_name(node: Node, process_name: str) -> None:
    """Kill all processes matching process_name by name."""
    ssh_run(
        node.user,
        node.ip,
        f"pkill -9 -f {process_name} 2>/dev/null; sleep 1",
        shell=node.shell,
    )


def disable_vector_agent(node: Node, node_name: str) -> None:
    """Stop and unregister a previously installed Vector launchd agent."""
    uid_result = ssh_run(node.user, node.ip, "id -u", shell=node.shell)
    if uid_result.returncode != 0:
        print(f"  [{node_name}] Warning: failed to get UID for Vector cleanup (continuing)")
        return
    uid = uid_result.stdout.strip()

    ssh_run(node.user, node.ip, f"launchctl bootout gui/{uid}/com.vector 2>/dev/null", timeout=30, shell=node.shell)
    _kill_process_by_name(node, "vector")
    ssh_run(node.user, node.ip, "rm -f ~/Library/LaunchAgents/com.vector.plist", timeout=10, shell=node.shell)


def install_node_tools(node: Node, *, needs_embedding: bool = False) -> None:
    """Install mlx-lm (always) and mlx-openai-server (only if node has embedding assignments)."""
    # Always remove legacy vllm-mlx first
    ssh_run(node.user, node.ip, "uv tool uninstall vllm-mlx 2>/dev/null || true", timeout=60, shell=node.shell)

    # Install mlx-lm
    result = ssh_run(
        node.user,
        node.ip,
        "uv tool install --force --python 3.13 mlx-lm --with 'httpx[socks]'",
        timeout=240,
        shell=node.shell,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        print(f"  Warning: mlx-lm install failed: {stderr or 'unknown error'} (continuing)")
        return
    print("  mlx-lm installed")

    if not needs_embedding:
        return

    # Install mlx-openai-server
    result = ssh_run(
        node.user,
        node.ip,
        "uv tool install --force --python 3.12 mlx-openai-server --with 'httpx[socks]'",
        timeout=240,
        shell=node.shell,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        print(f"  Warning: mlx-openai-server install failed: {stderr or 'unknown error'} (continuing)")
    else:
        print("  mlx-openai-server installed")


def deploy_node(
    node_name: str,
    config: ClusterConfig,
    *,
    dry_run: bool = False,
) -> list[str]:
    errors: list[str] = []
    node = config.nodes[node_name]
    slots = config.assignments.get(node_name, [])

    if not slots:
        return [f"{node_name}: no assignments found"]

    _require_resolved(node, node_name)

    if dry_run:
        for slot in slots:
            print(f"    [upload] com.mlx-lm-{slot.port}.plist ({slot.model}, port {slot.port})")
        print(f"    [restart] {len(slots)} launchd services")
        print(f"    [health] poll /v1/models on ports {', '.join(str(s.port) for s in slots)}")
        return errors

    uid_result = ssh_run(node.user, node.ip, "mkdir -p ~/logs ~/Library/LaunchAgents && id -u", shell=node.shell)
    if uid_result.returncode != 0:
        return [f"{node_name}: failed to get UID — {(uid_result.stderr or '').strip()}"]
    uid = uid_result.stdout.strip()

    # Clean up legacy vllm-mlx plists in a single SSH call
    legacy_cleanup = (
        f"for p in ~/Library/LaunchAgents/com.vllm-mlx-*.plist; do "
        f'[ -f "$p" ] && label=$(basename "$p" .plist) && '
        f'launchctl bootout gui/{uid}/$label 2>/dev/null; rm -f "$p"; done; '
        f"sudo rm -f /etc/newsyslog.d/vllm-mlx.conf"
    )
    legacy_result = ssh_run(node.user, node.ip, legacy_cleanup, shell=node.shell)
    if legacy_result.returncode == 0 and "com.vllm-mlx" not in (legacy_result.stderr or ""):
        pass  # no legacy plists or cleaned up silently
    else:
        print("  Cleaned up legacy vllm-mlx services")

    embedding_modes = {ServingMode.EMBEDDING, ServingMode.MLX_OPENAI_SERVER}
    needs_embedding = any(config.models[s.model].serving in embedding_modes for s in slots)
    install_node_tools(node, needs_embedding=needs_embedding)
    disable_vector_agent(node, node_name)

    deployed_ports: set[int] = set()

    for slot in slots:
        model = config.models[slot.model]
        plist_xml = generate_plist(model, slot, node)
        plist_name = f"com.mlx-lm-{slot.port}.plist"
        remote_plist = f"~/Library/LaunchAgents/{plist_name}"

        label = f"com.mlx-lm-{slot.port}"
        domain = f"gui/{uid}"
        plist_path = f"~/Library/LaunchAgents/{plist_name}"

        # Always replace the process behind the port. A stale mlx_lm.server can answer /v1/models
        # after the plist has changed to mlx-openai-server, producing a false healthy deploy.
        # Remove the old plist before bootout so launchd cannot re-spawn the previous command
        # between kill and bootstrap; then upload the new plist as the source of truth.
        stop_cmd = f"launchctl bootout {domain}/{label} 2>/dev/null; rm -f {plist_path}"
        ssh_run(node.user, node.ip, stop_cmd, timeout=30, shell=node.shell)
        _kill_port(node, slot.port)
        # launchd can need a short grace period after bootout/kill; without it, bootstrap
        # intermittently fails with code 5 even though the plist is valid and the port is free.
        ssh_run(node.user, node.ip, "sleep 2", timeout=5, shell=node.shell)
        result = scp_content(node.user, node.ip, plist_xml, remote_plist, shell=node.shell)
        if result.returncode != 0:
            errors.append(f"{node_name}: failed to upload {plist_name} — {(result.stderr or '').strip()}")
            continue

        bootstrap_cmd = f"launchctl bootstrap {domain} {plist_path}"
        result = ssh_run(node.user, node.ip, bootstrap_cmd, timeout=90, shell=node.shell)
        if result.returncode != 0:
            first_err = (result.stderr or "").strip() + " " + (result.stdout or "").strip()
            if "Bootstrap failed: 5" in first_err:
                retry_cmd = f"launchctl bootout {domain}/{label} 2>/dev/null; sleep 3; {bootstrap_cmd}"
                result = ssh_run(node.user, node.ip, retry_cmd, timeout=120, shell=node.shell)

        launch_failed = False
        if result.returncode != 0:
            err = (result.stderr or "").strip() + " " + (result.stdout or "").strip()
            errors.append(
                f"{node_name}: failed to start service on port {slot.port}\n"
                f"  error: {err.strip()}\n"
                f"  → Inspect: launchctl print {domain}/{label}; plutil -lint {plist_path}"
            )
            launch_failed = True

        if not launch_failed:
            # Verify launchd actually registered the service (catches silent failures and immediate crashes)
            verify = ssh_run(node.user, node.ip, f"launchctl list {label} 2>&1", timeout=10, shell=node.shell)
            if verify.returncode != 0:
                log_tail = _fetch_err_log(node, slot.port)
                msg = f"{node_name}: {label} not registered after deploy (launchctl list failed)"
                if log_tail:
                    indented_tail = "\n".join(f"    {line}" for line in log_tail.splitlines())
                    msg += f"\n  --- mlx-lm-{slot.port}.err ---\n{indented_tail}"
                errors.append(msg)
            elif '"PID"' not in (verify.stdout or ""):
                # Registered but no PID — crashed immediately, launchd may retry
                last_exit = next(
                    (line.strip() for line in (verify.stdout or "").splitlines() if "LastExitStatus" in line), ""
                )
                log_tail = _fetch_err_log(node, slot.port)
                msg = f"{node_name}: {label} crashed on startup ({last_exit or 'no PID'})"
                if log_tail:
                    indented_tail = "\n".join(f"    {line}" for line in log_tail.splitlines())
                    msg += f"\n  --- mlx-lm-{slot.port}.err ---\n{indented_tail}"
                print(f"  Warning: {msg}")

        deployed_ports.add(slot.port)

    newsyslog = NEWSYSLOG_CONF.format(user=node.user, home=node.home_dir)
    scp_content(node.user, node.ip, newsyslog, "/tmp/mlx-lm-newsyslog.conf", shell=node.shell)
    ssh_run(node.user, node.ip, "sudo mv /tmp/mlx-lm-newsyslog.conf /etc/newsyslog.d/mlx-lm.conf", shell=node.shell)

    # Note: ls 2>/dev/null || true is intentional — no plists present is not an error.
    ls_result = ssh_run(
        node.user, node.ip, "ls ~/Library/LaunchAgents/com.mlx-lm-*.plist 2>/dev/null || true", shell=node.shell
    )
    if ls_result.stdout.strip():
        for line in ls_result.stdout.strip().splitlines():
            filename = line.strip().split("/")[-1]
            try:
                port = int(filename.replace("com.mlx-lm-", "").replace(".plist", ""))
                if port not in deployed_ports:
                    print(f"  Removing stale plist for port {port}")
                    stale = f"com.mlx-lm-{port}"
                    # Note: 2>/dev/null on bootout is intentional — stale service may not be loaded.
                    cmd = f"launchctl bootout gui/{uid}/{stale} 2>/dev/null; rm ~/Library/LaunchAgents/{stale}.plist"
                    ssh_run(node.user, node.ip, cmd, shell=node.shell)
            except ValueError:
                continue

    return errors


def restart_litellm(config: ClusterConfig) -> bool:
    from thunder_forge.cluster.config import find_repo_root

    gw = config.gateway
    docker_dir = find_repo_root() / "docker"
    result = ssh_run(
        gw.user,
        gw.ip,
        f"cd {docker_dir} && docker compose restart litellm",
        timeout=60,
        shell=gw.shell,
    )
    return result.returncode == 0


def health_poll(
    ip: str,
    port: int,
    *,
    model: Model | None = None,
    timeout_secs: int = 300,
    interval: int = 5,
) -> bool:
    import time
    import urllib.error
    import urllib.request

    urls = [f"http://{ip}:{port}/v1/models"]
    if model and model.serving in {ServingMode.EMBEDDING, ServingMode.MLX_OPENAI_SERVER}:
        urls.append(f"http://{ip}:{port}/openapi.json")
    handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    deadline = time.monotonic() + timeout_secs

    while time.monotonic() < deadline:
        try:
            for url in urls:
                with opener.open(url, timeout=5):
                    pass
            return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(interval)

    return False


def _get_node_uid(node: Node) -> str | None:
    """Get the UID for a node. Returns None on failure."""
    result = ssh_run(node.user, node.ip, "id -u", shell=node.shell)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def restart_node_services(node_name: str, config: ClusterConfig) -> list[str]:
    """Restart all launchd services on a node: stop, wait for port release, then start."""
    errors: list[str] = []
    node = config.nodes[node_name]
    slots = config.assignments.get(node_name, [])
    if not slots:
        return [f"{node_name}: no assignments found"]
    uid = _get_node_uid(node)
    if not uid:
        return [f"{node_name}: failed to get UID"]
    for slot in slots:
        label = f"com.mlx-lm-{slot.port}"
        # Stop the service and ensure port is free before restarting
        ssh_run(node.user, node.ip, f"launchctl bootout gui/{uid}/{label} 2>/dev/null", shell=node.shell)
        _kill_port(node, slot.port)
        plist_path = f"~/Library/LaunchAgents/{label}.plist"
        result = ssh_run(
            node.user,
            node.ip,
            f"launchctl bootstrap gui/{uid} {plist_path}",
            timeout=90,
            shell=node.shell,
        )
        if result.returncode != 0:
            errors.append(f"{node_name}:{slot.port} ({slot.model}) — restart failed")
        else:
            print(f"  restarted {node_name}:{slot.port} ({slot.model})")
    return errors


def _kill_port(node: Node, port: int, *, timeout: int = 10) -> None:
    """Kill any process holding a port and wait for it to be free."""
    ssh_run(
        node.user,
        node.ip,
        f"lsof -ti :{port} | xargs kill -9 2>/dev/null; "
        f"for i in $(seq 1 {timeout}); do lsof -ti :{port} >/dev/null 2>&1 || break; sleep 1; done",
        timeout=timeout + 5,
        shell=node.shell,
    )


def stop_node_services(node_name: str, config: ClusterConfig) -> list[str]:
    """Stop all launchd services on a node via bootout, then kill any lingering processes."""
    errors: list[str] = []
    node = config.nodes[node_name]
    slots = config.assignments.get(node_name, [])
    if not slots:
        return [f"{node_name}: no assignments found"]
    uid = _get_node_uid(node)
    if not uid:
        return [f"{node_name}: failed to get UID"]
    for slot in slots:
        label = f"com.mlx-lm-{slot.port}"
        ssh_run(node.user, node.ip, f"launchctl bootout gui/{uid}/{label} 2>/dev/null", shell=node.shell)
        _kill_port(node, slot.port)
        print(f"  stopped {node_name}:{slot.port} ({slot.model})")
    return errors


def stop_litellm(config: ClusterConfig) -> bool:
    """Stop the LiteLLM proxy container."""
    from thunder_forge.cluster.config import find_repo_root

    gw = config.gateway
    docker_dir = find_repo_root() / "docker"
    result = ssh_run(
        gw.user,
        gw.ip,
        f"cd {docker_dir} && docker compose stop litellm",
        timeout=60,
        shell=gw.shell,
    )
    return result.returncode == 0


def run_restart(config: ClusterConfig, *, target_node: str | None = None, skip_gateway: bool = False) -> bool:
    """Restart inference services and optionally the LiteLLM proxy."""
    if target_node:
        if target_node not in config.assignments:
            print(f"Node '{target_node}' not found in assignments")
            return False
        nodes = [target_node]
    else:
        nodes = [n for n in config.assignments if config.nodes[n].role == NodeRole.NODE]

    all_ok = True
    print("Restarting node services...")
    with ThreadPoolExecutor(max_workers=max(1, len(nodes))) as pool:
        futures = {pool.submit(restart_node_services, n, config): n for n in nodes}
        for future in as_completed(futures):
            node_name = futures[future]
            errors = future.result()
            if errors:
                all_ok = False
                for err in errors:
                    print(f"  {err}")

    if not skip_gateway:
        print("\nRestarting LiteLLM...")
        if restart_litellm(config):
            print("  LiteLLM restarted")
        else:
            print("  LiteLLM restart failed")
            all_ok = False

    # Health poll
    print("\nWaiting for services to become healthy...")
    poll_tasks = []
    for node_name in nodes:
        node = config.nodes[node_name]
        for slot in config.assignments[node_name]:
            poll_tasks.append((node_name, node.ip, slot))
    if poll_tasks:
        with ThreadPoolExecutor(max_workers=len(poll_tasks)) as pool:
            futures = {
                pool.submit(health_poll, ip, slot.port, model=config.models[slot.model]): (node_name, slot)
                for node_name, ip, slot in poll_tasks
            }
            for future in as_completed(futures):
                node_name, slot = futures[future]
                healthy = future.result()
                status = "healthy" if healthy else "TIMEOUT"
                print(f"  {status} — {node_name}:{slot.port} ({slot.model})")
                if not healthy:
                    all_ok = False

    return all_ok


def run_stop(config: ClusterConfig, *, target_node: str | None = None, skip_gateway: bool = False) -> bool:
    """Stop inference services and optionally the LiteLLM proxy."""
    if target_node:
        if target_node not in config.assignments:
            print(f"Node '{target_node}' not found in assignments")
            return False
        nodes = [target_node]
    else:
        nodes = [n for n in config.assignments if config.nodes[n].role == NodeRole.NODE]

    all_ok = True
    print("Stopping node services...")
    with ThreadPoolExecutor(max_workers=max(1, len(nodes))) as pool:
        futures = {pool.submit(stop_node_services, n, config): n for n in nodes}
        for future in as_completed(futures):
            errors = future.result()
            if errors:
                all_ok = False
                for err in errors:
                    print(f"  {err}")

    if not skip_gateway:
        print("\nStopping LiteLLM...")
        if stop_litellm(config):
            print("  LiteLLM stopped")
        else:
            print("  LiteLLM stop failed")
            all_ok = False

    return all_ok


def run_deploy(config: ClusterConfig, *, target_node: str | None = None, dry_run: bool = False) -> bool:
    if target_node:
        if target_node not in config.assignments:
            print(f"Node '{target_node}' not found in assignments")
            return False
        deploy_nodes = [target_node]
    else:
        deploy_nodes = [n for n in config.assignments if config.nodes[n].role == NodeRole.NODE]

    if dry_run:
        print("\nDeployment plan:\n")
        for node_name in deploy_nodes:
            node = config.nodes[node_name]
            slots = config.assignments[node_name]
            print(f"  {node_name} ({node.ip}) — {len(slots)} services:")
            deploy_node(node_name, config, dry_run=True)
        try:
            gw = config.gateway
            print(f"\n  {config.gateway_name} ({gw.ip}) — gateway:")
            print("    [restart] LiteLLM proxy (docker compose restart litellm)")
        except ValueError:
            pass
        print("\nRun without --dry-run to execute.")
        return True

    # Acquire deploy lock
    if not _acquire_lock():
        print("Error: another deploy is already running (lock file exists and process is alive)")
        return False

    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, args=(stop_event,), daemon=True)
    heartbeat_thread.start()
    atexit.register(_release_lock)

    try:
        # Real deploy — continue on partial failure
        succeeded: list[str] = []
        failed: dict[str, str] = {}

        with ThreadPoolExecutor(max_workers=len(deploy_nodes)) as pool:
            futures = {pool.submit(deploy_node, n, config): n for n in deploy_nodes}
            for future in as_completed(futures):
                node_name = futures[future]
                errors = future.result()
                if errors:
                    failed[node_name] = "; ".join(errors)
                    for err in errors:
                        print(f"  ✗ {err}")
                else:
                    succeeded.append(node_name)
                    print(f"  ✓ {node_name} plists deployed")

        # LiteLLM restart — only if at least one node succeeded
        litellm_ok = False
        if succeeded:
            print("\nRestarting LiteLLM...")
            if restart_litellm(config):
                print("  ✓ LiteLLM restarted")
                litellm_ok = True
            else:
                if target_node:
                    print("  Warning: LiteLLM restart failed (non-fatal with --node)")
                    litellm_ok = True  # non-fatal
                else:
                    print("  ✗ LiteLLM restart failed")
        elif not target_node:
            print("\nSkipping LiteLLM restart — no nodes deployed successfully")

        # Health poll — only successfully deployed nodes
        if succeeded:
            print("\nWaiting for services to become healthy...")
            poll_tasks = []
            for node_name in succeeded:
                node = config.nodes[node_name]
                for slot in config.assignments[node_name]:
                    poll_tasks.append((node_name, node.ip, slot))
            with ThreadPoolExecutor(max_workers=len(poll_tasks)) as pool:
                futures = {
                    pool.submit(health_poll, ip, slot.port, model=config.models[slot.model]): (node_name, slot)
                    for node_name, ip, slot in poll_tasks
                }
                for future in as_completed(futures):
                    node_name, slot = futures[future]
                    healthy = future.result()
                    status = "✓ healthy" if healthy else "✗ timeout"
                    print(f"  {status} — {node_name}:{slot.port} ({slot.model})")
                    if not healthy:
                        failed[node_name] = failed.get(node_name, "") + f" port {slot.port} unhealthy"
                        log_tail = _fetch_err_log(config.nodes[node_name], slot.port)
                        if log_tail:
                            print(f"  --- {node_name}:mlx-lm-{slot.port}.err ---")
                            for line in log_tail.splitlines():
                                print(f"    {line}")

        # Summary
        total = len(deploy_nodes)
        ok_count = len(succeeded) - len([n for n in succeeded if n in failed])
        print(f"\nDeploy complete: {ok_count}/{total} nodes succeeded")
        for node_name in deploy_nodes:
            if node_name in failed:
                print(f"  ✗ {node_name} — {failed[node_name]}")
            else:
                slots = config.assignments[node_name]
                print(f"  ✓ {node_name} — {len(slots)} services running")

        if failed:
            print("\nFix failed nodes and re-run: thunder-forge deploy --node <name>")

        return len(failed) == 0 and litellm_ok
    finally:
        stop_event.set()
        _release_lock()
