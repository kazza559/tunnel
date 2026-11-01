"""Local HTTP/SOCKS5 -> verified TLS -> authenticated GOST on Modal."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import re
import secrets
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".runtime" / "session.json"
VERSION = "3.3.2"
ASSETS = {
    "linux": ("tar.gz", "162feb43b1c284dbea599cdfe898169931af875233060dedc19432c7a9d18274"),
}
REMOTE_PORT = 8080


def gost_binary(system):
    extension, checksum = ASSETS[system]
    name = "gost"
    binary = ROOT / ".cache" / VERSION / system / name
    if binary.exists():
        return binary
    asset = f"gost_{VERSION}_{system}_amd64.{extension}"
    print(f"Downloading and verifying {asset}...", flush=True)
    with urllib.request.urlopen(
        f"https://github.com/go-gost/gost/releases/download/v{VERSION}/{asset}", timeout=90
    ) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise RuntimeError("GOST archive checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        content = archive.extractfile(name).read()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(content)
    binary.chmod(0o700)
    return binary


def read_domains(path):
    domains = []
    for line in path.read_text(encoding="utf-8").splitlines():
        domain = line.split("#", 1)[0].strip().lower()
        if not domain:
            continue
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain):
            raise ValueError(f"Invalid domain: {domain!r}")
        labels = domain.split(".")
        if (len(labels) < 2 or len(domain) > 253 or labels[-1].isdigit()
                or any(not label or len(label) > 63 or label.startswith("-")
                       or label.endswith("-") for label in labels)):
            raise ValueError(f"Invalid domain: {domain!r}")
        domains.append(domain)
    if not domains:
        raise ValueError("Domain list must not be empty")
    return list(dict.fromkeys(domains))


def domain_rules(domains):
    return [f".{domain}:{port}" for domain in domains for port in (80, 443)]


def remote_config(domains, password):
    return {
        "services": [{"name": "egress", "addr": f":{REMOTE_PORT}",
                      "bypass": "allowed", "handler": {"type": "http", "auth": {
                          "username": "tunnel", "password": password}},
                      "listener": {"type": "tcp"}}],
        "bypasses": [{"name": "allowed", "whitelist": True,
                      "matchers": domain_rules(domains)}],
        "log": {"level": "warn"},
    }


def local_config(domains, password, hostname, port, local_port):
    return {"domains": domains, "password": password, "hostname": hostname,
            "port": port, "local_port": local_port}


def write_private(path, config):
    with open(path, "x", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        json.dump(config, stream)


def wait_local(process, port):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Local proxy exited; check its error output")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Local proxy did not become ready")


def stop():
    import modal
    from ssh_tunnel import stop_sandbox
    stop_sandbox()
    if not STATE.exists():
        print("No recorded Modal sandbox.")
        return
    sandbox_id = json.loads(STATE.read_text())["sandbox_id"]
    modal.Sandbox.from_id(sandbox_id).terminate(wait=True)
    STATE.unlink()
    print("Modal sandbox stopped. Close the local launcher if it is still running.")


def start(args):
    import modal
    if STATE.exists():
        raise RuntimeError("A session is recorded; run python tunnel.py stop first")
    domains = read_domains(args.domains)
    if args.smoke:
        domains = list(dict.fromkeys([*domains, "api.ipify.org"]))
    # Reserve the requested port before allocating any paid cloud resources.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    linux_gost = gost_binary("linux")
    image = modal.Image.debian_slim(python_version="3.12").add_local_file(
        linux_gost, "/usr/local/bin/gost", copy=True
    ).run_commands("chmod 755 /usr/local/bin/gost")
    password = secrets.token_urlsafe(32)
    app = modal.App.lookup("tunnel", create_if_missing=True)
    sandbox = None
    process = None
    STATE.parent.mkdir(exist_ok=True)
    # shortcut: one foreground session, restart manually after the sandbox lifetime expires.
    with tempfile.TemporaryDirectory(prefix="session-", dir=STATE.parent) as directory:
        try:
            sandbox = modal.Sandbox.create(
                "python", "-c",
                "import os; p='/tmp/gost.json'; "
                "f=os.fdopen(os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w'); "
                "f.write(os.environ.pop('TUNNEL_CONFIG')); f.close(); "
                "os.execv('/usr/local/bin/gost',['gost','-C',p])",
                app=app, image=image, cpu=0.25, memory=128, timeout=args.duration,
                encrypted_ports=[REMOTE_PORT], readiness_probe=modal.Probe.with_tcp(REMOTE_PORT),
                secrets=[modal.Secret.from_dict({"TUNNEL_CONFIG": json.dumps(remote_config(domains, password))})],
            )
            write_private(STATE, {"sandbox_id": sandbox.object_id})
            print(f"Waiting for Modal sandbox {sandbox.object_id}...", flush=True)
            sandbox.wait_until_ready(timeout=120)
            hostname, port = sandbox.tunnels()[REMOTE_PORT].tls_socket
            config_path = Path(directory) / "local.json"
            write_private(config_path, local_config(domains, password, hostname, port, args.port))
            process = subprocess.Popen([sys.executable, "-u", str(ROOT / "local_proxy.py"), str(config_path)])
            wait_local(process, args.port)
            print(f"HTTP CONNECT + SOCKS5: 127.0.0.1:{args.port}", flush=True)
            print("Selected domains -> Modal; other destinations -> DIRECT. Ctrl+C stops both.", flush=True)
            print(f'$env:HTTPS_PROXY = "http://127.0.0.1:{args.port}"', flush=True)
            print(f'$env:HTTP_PROXY = "http://127.0.0.1:{args.port}"', flush=True)
            if args.smoke:
                from smoke import check
                check(local_config(domains, password, hostname, port, args.port), sandbox)
                return
            while process.poll() is None:
                if sandbox.poll() is not None:
                    raise RuntimeError("Modal sandbox ended; start a new session")
                time.sleep(2)
            raise RuntimeError("Local proxy exited")
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if sandbox is not None:
                sandbox.terminate(wait=True)
                STATE.unlink(missing_ok=True)
                print("Modal sandbox stopped.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("start")
    run.add_argument("--smoke", action="store_true", help="Check live connectivity, then stop the sandbox")
    run.add_argument("--domains", type=Path, default=ROOT / "domains.txt")
    run.add_argument("--port", type=int, default=7890)
    run.add_argument("--duration", type=int, default=3600, help="Sandbox lifetime in seconds (default: 1 hour)")
    client = commands.add_parser("connect", help="Connect via pinned OpenSSH with automatic endpoint discovery")
    client.add_argument("--port", type=int, default=7890)
    client.add_argument("--trace", action="store_true", help="Log destination hostnames, never payloads or credentials")
    legacy = commands.add_parser("connect-wss", help="Explicitly use the previous WSS deployment")
    legacy.add_argument("--port", type=int, default=7890)
    legacy.add_argument("--trace", action="store_true")
    commands.add_parser("stop")
    args = parser.parse_args()
    if args.command == "start" and (not 1 <= args.port <= 65535 or not 60 <= args.duration <= 86400):
        parser.error("port must be 1..65535 and duration must be 60..86400 seconds")
    try:
        if args.command == "connect":
            if not 1 <= args.port <= 65535:
                parser.error("port must be 1..65535")
            import asyncio
            from ssh_tunnel import connect
            asyncio.run(connect(args))
        elif args.command == "connect-wss":
            if not 1 <= args.port <= 65535:
                parser.error("port must be 1..65535")
            import asyncio
            from local_proxy import serve
            config = json.loads((ROOT / ".runtime" / "deployed.json").read_text())
            if not config.get("url", "").startswith("wss://"):
                raise ValueError("Run python deploy.py first")
            config.update(local_port=args.port, trace=args.trace)
            print(f"HTTP CONNECT + SOCKS5: 127.0.0.1:{args.port}", flush=True)
            asyncio.run(serve(config))
        elif args.command == "start":
            start(args)
        else:
            stop()
    except KeyboardInterrupt:
        print("Stopped.")
    except Exception as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
