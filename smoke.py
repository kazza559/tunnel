"""Explicit live checks; no model calls or application credentials required."""
import base64
import ipaddress
import os
import socket
import ssl
import subprocess


def remote_status(config, target, password=None):
    with socket.create_connection((config["hostname"], config["port"]), timeout=15) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=config["hostname"]) as sock:
            auth = ""
            if password is not None:
                token = base64.b64encode(f"tunnel:{password}".encode()).decode()
                auth = f"Proxy-Authorization: Basic {token}\r\n"
            sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{auth}\r\n".encode())
            response = b""
            while b"\r\n" not in response:
                data = sock.recv(1024)
                if not data or len(response) > 65536:
                    raise RuntimeError("Invalid remote proxy response")
                response += data
            return int(response.split(b"\r\n", 1)[0].split()[1])


def curl(port, url, socks=False, status_only=False):
    command = ["curl.exe" if os.name == "nt" else "curl", "--silent", "--show-error",
               "--max-time", "30", "--noproxy", "", "--proxy",
               f"{'socks5h' if socks else 'http'}://127.0.0.1:{port}"]
    if status_only:
        command += ["--output", "NUL" if os.name == "nt" else "/dev/null", "--write-out", "%{http_code}"]
    command.append(url)
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()


def check(config, sandbox):
    for label, target, password, expected in (
        ("Unauthenticated access", "api.openai.com:443", None, 407),
        ("Wrong password", "api.openai.com:443", "wrong", 407),
        ("Destination restriction", "example.com:443", config["password"], 403),
        ("Port restriction", "api.openai.com:22", config["password"], 403),
    ):
        actual = remote_status(config, target, password)
        if actual != expected:
            raise RuntimeError(f"{label}: expected {expected}, got {actual}")
        print(f"PASS {label}: {actual}", flush=True)
    remote = sandbox.exec("python", "-c", "import urllib.request; print(urllib.request.urlopen('https://api.ipify.org',timeout=20).read().decode())")
    remote_ip = str(ipaddress.ip_address(remote.stdout.read().strip()))
    for socks in (False, True):
        local_ip = str(ipaddress.ip_address(curl(config["local_port"], "https://api.ipify.org", socks=socks)))
        if local_ip != remote_ip:
            raise RuntimeError("Proxy IP differs from the sandbox egress IP")
        print(f"PASS {'SOCKS5' if socks else 'HTTP CONNECT'}: Modal egress {local_ip}", flush=True)
    for url in ("https://api.openai.com/v1/models", "https://api.anthropic.com/v1/messages"):
        status = curl(config["local_port"], url, status_only=True)
        if status == "000":
            raise RuntimeError("No HTTPS response")
        print(f"PASS HTTPS transport: {url} -> {status} (unauthenticated)", flush=True)
    status = curl(config["local_port"], "https://example.com", status_only=True)
    if status != "200":
        raise RuntimeError(f"DIRECT diagnostic returned {status}")
    print("PASS unselected domain: DIRECT example.com -> 200", flush=True)
