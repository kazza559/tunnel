# Live verification — 2026-11-01

Deployment: https://kazimhung559--v0z.us-east.modal.run

The CLI requests below ran before the endpoint label changed to `v0z`; after renaming, HTTP CONNECT and SOCKS5 were checked again successfully through the new URL.

Local frontend: `127.0.0.1:7890`, WSS transport to Modal. Both clients used their existing local account login, official provider endpoints and this local proxy. No application tokens were copied to Modal. The prompt instructed both clients not to use tools; no tool calls were observed in the successful answers. Claude tools/MCP were explicitly disabled for its run.

Prompt: “Do not use any tools or inspect files. Answer these two questions with exactly two lines, no explanation: What is 2 + 5? What is the capital of Vietnam?”

| Client | Exit code | Answer | Elapsed |
|---|---|---|---|
| Codex CLI 0.166.0 (`gpt-6.1-sol`) | 0 | `7` / `Hanoi` | 19.1 seconds |
| Claude Code 2.1.316 (`claude-opus-5-5`) | 0 | `7` / `Hanoi` | 7.4 seconds |

Proxy trace confirmed successful Modal routes to `chatgpt.com`, `ab.chatgpt.com` and `api.anthropic.com` during the runs. Detailed results are gitignored in `.runtime/codex-result.json` and `.runtime/claude-result.json`. The CLI test has a 150-second timeout and does not edit user configuration.

Deployed policy checks: unauthenticated CONNECT = 407; wrong password = 407; unlisted domain = 403; unlisted port = 403. Actual egress check through HTTP CONNECT returned a Modal-side IP. The stable deployment was also checked with both HTTP CONNECT and SOCKS5; both returned the same egress IP. All 22 automated tests passed.

This validates short authenticated model requests. Long-running sessions, token expiry/refresh, browser sign-in and use from the company network have not been tested. The deployed endpoint persists, but any individual WSS connection may end after one hour; the client must reconnect. Trace logging was enabled only for validation.
