"""Deploy a stable Modal endpoint and save private local connection settings."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

import modal
from tunnel import ROOT, read_domains, write_private

CONFIG = ROOT / ".runtime" / "deployed.json"


def main():
    CONFIG.parent.mkdir(exist_ok=True)
    if CONFIG.exists():
        config = json.loads(CONFIG.read_text())
    else:
        config = {"password": secrets.token_urlsafe(32),
                  "secret_name": "tunnel-auth-" + secrets.token_hex(4), "local_port": 7890}
        # Save before provisioning so an interrupted deployment can reuse its secret.
        write_private(CONFIG, config)
    modal.Secret.objects.create(config["secret_name"], {"TUNNEL_PASSWORD": config["password"]}, allow_existing=True)
    subprocess.run([sys.executable, "-m", "modal", "deploy", str(ROOT / "modal_app.py")],
                   env=dict(os.environ, TUNNEL_SECRET_NAME=config["secret_name"], PYTHONIOENCODING="utf-8", PYTHONUTF8="1"), check=True)
    url = modal.Function.from_name("tunnel", "proxy").get_web_url()
    if not url or not url.startswith("https://"):
        raise RuntimeError("Deployment did not return an HTTPS endpoint")
    config.update(url="wss://" + url.removeprefix("https://").rstrip("/") + "/ws",
                  domains=read_domains(ROOT / "domains.txt"))
    temporary = CONFIG.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    write_private(temporary, config)
    temporary.replace(CONFIG)
    print(f"Deployed: {url}")
    print("Start local proxy: python tunnel.py connect")


if __name__ == "__main__":
    main()
