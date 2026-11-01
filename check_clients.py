import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unicodedata

root = Path(__file__).resolve().parent
client = sys.argv[1]
if client not in ("codex", "claude"):
    raise ValueError("Usage: python check_clients.py codex|claude")
work = root / ".runtime" / "client-test"
work.mkdir(exist_ok=True)
env = os.environ.copy()
for key in list(env):
    if key.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "WS_PROXY", "WSS_PROXY",
                        "OPENAI_BASE_URL", "OPENAI_API_BASE", "ANTHROPIC_BASE_URL", "CODEX_REFRESH_TOKEN_URL_OVERRIDE",
                        "CODEX_REVOKE_TOKEN_URL_OVERRIDE", "CLAUDECODE"):
        env.pop(key)
env.update(HTTPS_PROXY="http://127.0.0.1:7890", HTTP_PROXY="http://127.0.0.1:7890",
           ALL_PROXY="http://127.0.0.1:7890", WS_PROXY="http://127.0.0.1:7890", WSS_PROXY="http://127.0.0.1:7890",
           NO_PROXY="localhost,127.0.0.1,::1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
prompt = "Do not use any tools or inspect files. Answer these two questions with exactly two lines, no explanation: What is 2 + 5? What is the capital of Vietnam?"
if client == "codex":
    node = shutil.which("node")
    script = Path(node).parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    command = [node, str(script), "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
               "--sandbox", "read-only", "--color", "never", "--json", "--model", "gpt-6.1-sol", prompt]
else:
    command = [shutil.which("claude"), "--safe-mode", "--restricted", "-p", prompt,
               "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--no-session-persistence", "--output-format", "json"]
print(f"Starting {client} with official endpoints through local proxy...", flush=True)
started=time.monotonic()
process = subprocess.Popen(command, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
try:
    stdout, stderr = process.communicate(timeout=150)
except subprocess.TimeoutExpired:
    process.kill()
    stdout, stderr = process.communicate()
    raise RuntimeError(f"{client} exceeded 150 seconds")
(root / ".runtime" / f"{client}.stdout.log").write_text(stdout, encoding="utf-8")
(root / ".runtime" / f"{client}.stderr.log").write_text(stderr, encoding="utf-8")
answer = ""
if client == "codex":
    for line in stdout.splitlines():
        try: event=json.loads(line)
        except ValueError: continue
        item=event.get("item",{})
        if item.get("type") == "agent_message": answer += item.get("text","")
else:
    try: answer=json.loads(stdout).get("result", "")
    except ValueError: pass
normalized = ''.join(c for c in unicodedata.normalize("NFD",answer.lower()) if not unicodedata.combining(c))
passed = process.returncode == 0 and "7" in normalized and ("hanoi" in normalized or "ha noi" in normalized)
result={"client":client,"pid":process.pid,"exit_code":process.returncode,"elapsed_seconds":round(time.monotonic()-started,1),"answer":answer,"passed":passed}
(root/".runtime"/f"{client}-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=True), flush=True)
if not passed:
    print('Diagnostic output:',(stderr or stdout)[-3500:],flush=True)
    sys.exit(1)
