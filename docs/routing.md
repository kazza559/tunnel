# Domain filter cho Codex và Claude Code

Cấu hình nằm trong **`domains.txt`**. Một dòng là một domain gốc; domain đó và mọi subdomain của nó đi qua SSH/Modal. Các đích còn lại đi DIRECT từ A. Mỗi ứng dụng vẫn phải trỏ proxy local; đây không phải rule theo tên process và không phải TUN cho toàn máy.

```text
api.openai.com / auth.openai.com / chatgpt.com     -> SSH -> Modal
api.anthropic.com / platform.claude.com / claude.ai -> SSH -> Modal
github.com / npm / site nội bộ / domain khác        -> DIRECT
```

## Danh sách và nguồn đối chiếu

| Domain gốc | Phạm vi | Căn cứ |
|---|---|---|
| `openai.com` | API, Realtime, OAuth/device login | `codex-proxy/modal_app.py`, `cloud_auth.py` |
| `chatgpt.com` | Codex Responses, Apps/MCP và backend tài khoản | `codex-proxy/modal_app.py` |
| `oaistatic.com` | Tài nguyên first-party, gồm `persistent.oaistatic.com` | `codex-proxy/README.md` |
| `oaiusercontent.com`, `oaistatsig.com` | Nội dung/host phụ trợ first-party | Phần bổ sung cho hệ sinh thái ChatGPT |
| `anthropic.com` | API và các subdomain first-party | `claude-proxy/src/main.rs` |
| `claude.com` | Authorize, platform token exchange/refresh, SSO | `claude-proxy/claude-auth-providers/src/claude_code/login.rs`, README |
| `claude.ai` | Login và `downloads.claude.ai` | `claude-proxy/modal_app.py`, README |
| `claudeusercontent.com`, `claudemcpcontent.com` | Nội dung, bridge/widget first-party | Phần bổ sung cho tính năng Claude |

Đã đọc nguồn tại `D:\code\codex-proxy` và `D:\code\claude-proxy`; không sửa hai repo đó. Filter chỉ lấy hostname, không sao chép logic đổi token, sửa payload hoặc OAuth của gateway cũ.

Các host phụ trợ first-party không phải lời bảo đảm mọi tính năng browser/plugin đã được kiểm chứng. Thêm domain khi xác nhận tính năng cụ thể cần nó; không đưa cả Internet vào rule.

## Sửa và áp dụng

```text
# domains.txt
openai.com
chatgpt.com
anthropic.com
claude.ai
claude.com
```

Ví dụ trên minh họa cú pháp; file mặc định còn có các domain CDN/content trong bảng. Cho phép dòng trống và comment `#`. Không nhập `https://`, port, IP hoặc wildcard; parser sẽ từ chối.

Dừng frontend hiện tại, sửa file rồi chạy `python tunnel.py connect`. Tên sandbox có fingerprint của filter, nên cấu hình mới có sandbox mới; local frontend và GOST remote nhận cùng danh sách. Sandbox cấu hình cũ sẽ idle/expire, hoặc dọn qua dashboard nếu không còn dùng.

Nếu domain đã chọn mà SSH lỗi, client trả lỗi, không chuyển DIRECT. Local chọn domain ở mọi port nhưng GOST remote chỉ cho 80/443; các port khác bị từ chối. DNS đích của domain được chọn được xử lý ở Modal khi ứng dụng gửi hostname qua HTTP CONNECT hoặc SOCKS hostname mode.

## Shared domain và giới hạn path

Các repo tham chiếu có GitHub URL như `github.com/openai/...` hoặc thư viện/installer trên npm, Google Storage. File mặc định **không** đưa các host dùng chung đó qua Modal, vì rule hostname sẽ áp dụng cho toàn host, không riêng package/repo của OpenAI/Anthropic.

Qua HTTPS, frontend không đọc path `/openai/...`. Nếu bạn thực sự cần tải plugin trên host dùng chung bị chặn, có thể thêm host đó vào file có chủ đích; khi đó mọi request tới host ấy qua frontend cũng sẽ đi Modal. Đừng dùng rule substring `openai` trên URL hoặc tên host.

Site nội bộ không match filter đi DIRECT, không tự đi B. Nếu chỉ B truy cập được site đó thì giữ proxy B riêng; repo chưa tích hợp route B.

## Chẩn đoán và test

`api.ipify.org` đã bỏ khỏi filter production. Gọi host đó qua proxy mặc định sẽ đi DIRECT và **không chứng minh egress SSH**. Chỉ sandbox `start --smoke` thêm domain diagnostic này vào cấu hình thử của chính phiên đó; không sửa file production.

Để kiểm tra đúng route mặc định:

1. Bật frontend với `--trace`, chờ `SSH ready`.
2. Chạy `python check_clients.py codex` và `python check_clients.py claude`.
3. Xem các dòng `MODAL chatgpt.com:443` hoặc `MODAL api.anthropic.com:443`; tắt trace sau khi kiểm tra.

Một CONNECT đã thành công mà API trả 401 chưa có credentials vẫn xác nhận đường mạng, nhưng không xác nhận tài khoản dùng được. Hai CLI smoke mới là lượt kiểm tra đã đăng nhập. Unit test cũng kiểm tra API/auth/CDN được chọn, còn shared host, domain nội bộ, domain giả và dịch vụ diagnostic giữ DIRECT.
