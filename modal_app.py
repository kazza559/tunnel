"""Persistent authenticated WebSocket egress, deployed with python deploy.py."""
import json
import os
from pathlib import Path
import subprocess

import modal
from tunnel import ROOT, gost_binary, read_domains, remote_config, write_private

app = modal.App("tunnel")
image = None
if modal.is_local():
    image = (modal.Image.debian_slim(python_version="3.12")
             .add_local_file(gost_binary("linux"), "/usr/local/bin/gost", copy=True)
             .add_local_file(ROOT / "domains.txt", "/app/domains.txt", copy=True)
             .run_commands("chmod 755 /usr/local/bin/gost")
             .add_local_python_source("tunnel"))


@app.function(image=image, secrets=[modal.Secret.from_name(os.environ["TUNNEL_SECRET_NAME"])],
              env={"TUNNEL_SECRET_NAME": os.environ["TUNNEL_SECRET_NAME"]},
              cpu=0.25, memory=128, max_containers=1, scaledown_window=300, timeout=3600)
@modal.concurrent(max_inputs=100)
@modal.web_server(8080, startup_timeout=60, label="v0z")
def proxy():
    config = remote_config(read_domains(Path("/app/domains.txt")), os.environ.pop("TUNNEL_PASSWORD"))
    config["services"][0]["listener"] = {"type": "ws", "metadata": {"path": "/ws"}}
    path = Path("/tmp/gost-deployed.json")
    path.unlink(missing_ok=True)
    write_private(path, config)
    subprocess.Popen(["/usr/local/bin/gost", "-C", str(path)])
