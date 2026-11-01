"""Sandbox entrypoint: restricted OpenSSH forwarding to a loopback GOST proxy."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import time


def private_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        stream.write(text)


def main():
    runtime = Path("/run/tunnel")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    host_key = runtime / "host_ed25519"
    private_text(host_key, os.environ.pop("SSH_HOST_KEY"))
    authorized = Path("/home/tunnel/.ssh/authorized_keys")
    private_text(authorized, os.environ.pop("SSH_AUTHORIZED_KEY") + "\n")
    account = pwd.getpwnam("tunnel")
    authorized.parent.chmod(0o700)
    os.chown(authorized.parent, account.pw_uid, account.pw_gid)
    os.chown(authorized, account.pw_uid, account.pw_gid)
    config = json.loads(os.environ.pop("GOST_CONFIG"))
    private_text(runtime / "gost.json", json.dumps(config))
    private_text(runtime / "sshd_config", """Port 2222
ListenAddress 0.0.0.0
HostKey /run/tunnel/host_ed25519
PidFile /run/tunnel/sshd.pid
AuthorizedKeysFile /home/tunnel/.ssh/authorized_keys
AllowUsers tunnel
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
UsePAM no
AllowTcpForwarding local
PermitOpen 127.0.0.1:8080
AllowAgentForwarding no
AllowStreamLocalForwarding no
PermitTunnel no
PermitUserRC no
X11Forwarding no
PermitTTY no
GatewayPorts no
MaxSessions 0
LogLevel ERROR
""")
    Path("/run/sshd").mkdir(exist_ok=True)
    subprocess.run(["/usr/sbin/sshd", "-t", "-f", str(runtime / "sshd_config")], check=True)
    gost = subprocess.Popen(["/usr/local/bin/gost", "-C", str(runtime / "gost.json")])
    sshd = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(runtime / "sshd_config")])
    try:
        while gost.poll() is None and sshd.poll() is None:
            time.sleep(1)
        raise RuntimeError("SSH or proxy process stopped")
    finally:
        for process in (sshd, gost):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
