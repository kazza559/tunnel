# tunnel

Proxy local cho Codex, Claude Code và ứng dụng hỗ trợ HTTP CONNECT/SOCKS5. Chỉ domain trong `domains.txt` đi qua Modal; các đích khác mở kết nối TCP trực tiếp từ máy local. Endpoint và payload của ứng dụng giữ nguyên, credentials của Codex/Claude vẫn ở máy local.

```text
Ứng dụng -> 127.0.0.1:7890
             |-- domain được chọn -> WSS /ws -> Modal -> Internet
             `-- domain khác       -> DIRECT
```

## Trạng thái đã kiểm chứng

- Modal App: `tunnel`; endpoint label: `v0z`.
- Endpoint hiện tại: `https://kazimhung559--v0z.us-east.modal.run`.
- Local: `127.0.0.1:7890`, cùng cổng cho HTTP CONNECT và SOCKS5.
- Codex CLI và Claude Code đã trả lời thành công hai câu hỏi qua tunnel; 22 test tự động đã qua. Xem [bằng chứng và phạm vi kiểm tra](VERIFICATION.md).
- Sau đổi label sang `v0z`, đã kiểm tra lại HTTP CONNECT/SOCKS5; các lượt model request được thực hiện trước lần đổi label.

Endpoint là transport WebSocket có xác thực, không phải trang web để mở trực tiếp và không phải OpenAI-compatible API base URL.

## Bắt đầu trên Windows

Cần Python 3.11+, tài khoản Modal; thêm `curl.exe` cho kiểm tra mạng. Codex/Claude chỉ cần khi muốn chạy model smoke test.

```powershell
git clone git@github.com:kazza559/tunnel.git
cd tunnel
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# Chỉ làm nếu máy này chưa đăng nhập Modal:
python -m modal setup
```

**Nếu tạo deployment mới:**

```powershell
python deploy.py
python tunnel.py connect
```

**Nếu tiếp tục deployment đang có:** cần khôi phục riêng file `.runtime/deployed.json` từ bản sao an toàn của chính bạn rồi chạy `python tunnel.py connect`. File này chứa mật khẩu, không có trên GitHub. Không chạy deploy với config trống chỉ để lấy lại kết nối: nó tạo secret mới và có thể thay secret của deployment cùng tên. Xem [vận hành và chuyển máy](docs/operations.md).

Giữ terminal proxy mở. Trong terminal ứng dụng riêng:

```powershell
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = 'localhost,127.0.0.1,::1'

curl.exe --silent --show-error --fail --max-time 30 --noproxy "" --proxy http://127.0.0.1:7890 https://api.ipify.org
curl.exe --silent --show-error --fail --max-time 30 --noproxy "" --proxy socks5h://127.0.0.1:7890 https://api.ipify.org
```

Hai lệnh kiểm tra phải trả về IP egress của Modal. Một mình biến proxy không gỡ custom API endpoint hoặc auth override đã cấu hình trước đây; xem [cấu hình ứng dụng](docs/clients.md).

## Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [Kiến trúc](docs/architecture.md) | Đường dữ liệu, DNS, xác thực, domain rule, giới hạn quan sát |
| [Vận hành](docs/operations.md) | Deploy/redeploy, chạy nền, dừng, đổi label, chuyển máy và xử lý lỗi |
| [Codex/Claude và kiểm tra](docs/clients.md) | Proxy theo tiến trình, loại override cũ, kiểm tra CLI và browser |
| [Kết quả kiểm chứng](VERIFICATION.md) | Phiên bản CLI, câu hỏi, kết quả và những phần chưa kiểm tra |

## Các lệnh chính

| Lệnh | Tác dụng |
|---|---|
| `python deploy.py` | Deploy endpoint cố định, lưu config riêng trong `.runtime/deployed.json` |
| `python tunnel.py connect` | Mở proxy local tới deployment cố định |
| `python tunnel.py connect --port 7891` | Dùng cổng local khác |
| `python tunnel.py connect --trace` | Ghi hostname/port để chẩn đoán, không ghi payload/token |
| `python tunnel.py start --duration 300 --smoke` | Sandbox riêng tối đa 5 phút; kiểm tra rồi dọn sandbox |
| `python tunnel.py stop` | Dừng sandbox theo phiên đã ghi lại; không dừng deployment/local frontend cố định |
| `python -m modal app stop tunnel` | Dừng deployment và container của App `tunnel` |
| `python -m unittest -v` | Test local, không gọi model hay tạo tài nguyên Modal |
| `python check_clients.py codex` | Gửi câu hỏi qua Codex thật, dùng login local và quota tài khoản |
| `python check_clients.py claude` | Gửi câu hỏi qua Claude Code thật, dùng login local và quota tài khoản |

## Giới hạn cần biết

- Chưa có TUN, system proxy, tự khởi động cùng Windows hoặc ghép route qua B; chỉ ứng dụng trỏ proxy mới đi qua đây.
- DIRECT là TCP trực tiếp, không tự kế thừa HTTP proxy/PAC của công ty. Tài nguyên chỉ truy cập được qua B vẫn cần đường riêng của B.
- Dùng SOCKS hostname (`socks5h`); nếu ứng dụng resolve thành IP trước, proxy không biết domain gốc để chọn Modal.
- TCP, HTTP và HTTPS/WSS; SOCKS5 chỉ CONNECT, không UDP/BIND. Idle 5 phút sẽ đóng; mỗi kết nối WSS tối đa 1 giờ, ứng dụng cần kết nối lại.
- Mỗi đích mở một WSS riêng. Deployment cố định tồn tại, container có thể scale xuống khi hết kết nối và có cold start khi dùng lại.
- Có phí compute/egress của Modal. IP ra là IP cloud và có thể thay đổi. Chưa kiểm tra refresh token, phiên dài hoặc chính mạng công ty.
- Mạng vẫn thấy hostname/IP Modal và metadata; TLS interception có CA được máy tin cậy hoặc endpoint monitoring có thể thấy thêm. Tunnel không cam kết che khỏi mọi hệ thống giám sát.

`.runtime/`, `.cache/`, `.venv/` và bytecode được gitignore. Các key trong `fixtures/` chỉ là dữ liệu test localhost, không dùng cho deployment. Repo không chứa login hay API key của Codex/Claude.
