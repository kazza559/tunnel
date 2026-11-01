"""OpenSSH transport with pinned host identity and live named-Sandbox discovery."""
import asyncio
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

from local_proxy import Proxy
from tunnel import ROOT, gost_binary, read_domains, remote_config, wait_local, write_private

RUNTIME = ROOT / ".runtime"
STATE = RUNTIME / "ssh-session.json"
HOST_ALIAS = "tunnel-modal"
SSH_PORT = 2222


def prepare_keys():
    if not shutil.which("ssh") or not shutil.which("ssh-keygen"):
        raise RuntimeError("OpenSSH client and ssh-keygen must be installed")
    RUNTIME.mkdir(exist_ok=True)
    for name in ("ssh_client_ed25519", "ssh_host_ed25519"):
        key = RUNTIME / name
        if not key.exists():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        if os.name == "nt":
            result = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
            sid = next(csv.reader([result.strip()]))[-1]
            subprocess.run(["icacls", str(key), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
                           check=True, stdout=subprocess.DEVNULL)
        else:
            key.chmod(0o600)
        if not key.with_suffix(".pub").exists():
            public = subprocess.check_output(["ssh-keygen", "-y", "-f", str(key)], text=True)
            key.with_suffix(".pub").write_text(public, encoding="utf-8")
    public = (RUNTIME / "ssh_host_ed25519.pub").read_text().split()
    (RUNTIME / "ssh_known_hosts").write_text(f"{HOST_ALIAS} {public[0]} {public[1]}\n", encoding="utf-8")
    (RUNTIME / "ssh_config").write_text("", encoding="utf-8")


def sandbox_name(domains):
    public = (RUNTIME / "ssh_client_ed25519.pub").read_text()
    host = (RUNTIME / "ssh_host_ed25519.pub").read_text()
    server = (ROOT / "ssh_server.py").read_text(encoding="utf-8")
    digest = hashlib.sha256(json.dumps([domains, public, host, server]).encode()).hexdigest()[:12]
    return "ssh-egress-" + digest


def ensure_sandbox(domains):
    import modal
    name = sandbox_name(domains)
    try:
        sandbox = modal.Sandbox.from_name("tunnel", name)
    except modal.exception.NotFoundError:
        config = remote_config(domains, "unused")
        config["services"][0]["addr"] = "127.0.0.1:8080"
        config["services"][0]["handler"].pop("auth")
        image = (modal.Image.debian_slim(python_version="3.12").apt_install("openssh-server")
                 .run_commands("useradd --create-home --shell /usr/sbin/nologin tunnel", "usermod -p '*' tunnel")
                 .add_local_file(gost_binary("linux"), "/usr/local/bin/gost", copy=True)
                 .add_local_file(ROOT / "ssh_server.py", "/app/ssh_server.py", copy=True)
                 .run_commands("chmod 755 /usr/local/bin/gost"))
        try:
            sandbox = modal.Sandbox.create(
                "python", "/app/ssh_server.py", app=modal.App.lookup("tunnel", create_if_missing=True),
                name=name, image=image, cpu=0.25, memory=128, timeout=86400, idle_timeout=300,
                unencrypted_ports=[SSH_PORT], readiness_probe=modal.Probe.with_tcp(SSH_PORT),
                secrets=[modal.Secret.from_dict({
                    "SSH_HOST_KEY": (RUNTIME / "ssh_host_ed25519").read_text(),
                    "SSH_AUTHORIZED_KEY": (RUNTIME / "ssh_client_ed25519.pub").read_text().strip(),
                    "GOST_CONFIG": json.dumps(config),
                })],
            )
        except modal.exception.AlreadyExistsError:
            sandbox = modal.Sandbox.from_name("tunnel", name)
    sandbox.wait_until_ready(timeout=120)
    hostname, port = sandbox.tunnels()[SSH_PORT].tcp_socket
    temporary = STATE.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    write_private(temporary, {"sandbox_id": sandbox.object_id, "name": name,
                              "hostname": hostname, "port": port})
    temporary.replace(STATE)
    return hostname, port


def ssh_command(hostname, port, local_port):
    return ["ssh", "-F", str(RUNTIME / "ssh_config"), "-N", "-T",
            "-i", str(RUNTIME / "ssh_client_ed25519"), "-p", str(port),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
            "-o", "StrictHostKeyChecking=yes", "-o", f"HostKeyAlias={HOST_ALIAS}",
            "-o", f"UserKnownHostsFile={RUNTIME / 'ssh_known_hosts'}",
            "-o", f"GlobalKnownHostsFile={'NUL' if os.name == 'nt' else '/dev/null'}",
            "-o", "CheckHostIP=no", "-o", "UpdateHostKeys=no",
            "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
            "-o", "LogLevel=ERROR", "-L", f"127.0.0.1:{local_port}:127.0.0.1:8080",
            f"tunnel@{hostname}"]


async def supervise(config, stop_event):
    delay = 1
    process = None
    try:
        with open(RUNTIME / "ssh.stderr.log", "a", encoding="utf-8") as log:
            while not stop_event.is_set():
                config["ssh_ready"] = False
                try:
                    hostname, port = await asyncio.to_thread(ensure_sandbox, config["domains"])
                    process = subprocess.Popen(ssh_command(hostname, port, config["ssh_port"]),
                                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
                    await asyncio.to_thread(wait_local, process, config["ssh_port"])
                    config["ssh_ready"] = True
                    delay = 1
                    print(f"SSH ready: {hostname}:{port}; host identity pinned as {HOST_ALIAS}", flush=True)
                    await asyncio.to_thread(process.wait)
                    raise OSError("SSH disconnected")
                except Exception as exc:
                    print(f"{type(exc).__name__}: {exc}; rediscovering endpoint in {delay}s", flush=True)
                finally:
                    config["ssh_ready"] = False
                    if process is not None and process.poll() is None:
                        process.terminate()
                        try:
                            await asyncio.wait_for(asyncio.to_thread(process.wait), 5)
                        except asyncio.TimeoutError:
                            process.kill()
                            await asyncio.to_thread(process.wait)
                    process = None
                try:
                    await asyncio.wait_for(stop_event.wait(), delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, 15)
    finally:
        config["ssh_ready"] = False
        if process is not None and process.poll() is None:
            process.terminate()
            await asyncio.to_thread(process.wait)


async def connect(args):
    # shortcut: one manager per runtime directory; add a process lock before multi-manager use.
    prepare_keys()
    domains = read_domains(ROOT / "domains.txt")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        ssh_port = probe.getsockname()[1]
    config = {"domains": domains, "ssh_port": ssh_port, "ssh_ready": False, "trace": args.trace}
    proxy = Proxy(config)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", args.port, limit=65536)
    stop_event = asyncio.Event()
    task = asyncio.create_task(supervise(config, stop_event))
    print(f"Local HTTP CONNECT + SOCKS5: 127.0.0.1:{args.port}; waiting for SSH", flush=True)
    try:
        async with server:
            await task
    finally:
        stop_event.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def stop_sandbox():
    import modal
    if STATE.exists():
        sandbox_id = json.loads(STATE.read_text())["sandbox_id"]
        try:
            modal.Sandbox.from_id(sandbox_id).terminate(wait=True)
        except modal.exception.NotFoundError:
            pass
        STATE.unlink()
        print("SSH sandbox stopped. A running frontend will recreate it; stop the frontend first.")
    else:
        print("No recorded SSH sandbox.")
