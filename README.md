# tunnel

Proxy local HTTP CONNECT/SOCKS5 cho Codex và Claude Code. Transport mặc định là **OpenSSH** tới Modal; chỉ domain được chọn đi cloud, các đích khác mở TCP trực tiếp từ máy local. Không đổi API endpoint, payload hoặc token của ứng dụng.

```text
Ứng dụng -> 127.0.0.1:7890
             |-- domain được chọn -> SSH port forward -> GOST trên Modal -> Internet
             `-- domain khác       -> DIRECT
```

## Chạy

Cần Python 3.11+, OpenSSH client (`ssh`, `ssh-keygen`), tài khoản Modal và quyền tạo sandbox. Linux sandbox dùng GOST 3.3.2 và openssh-server; Windows không cần chạy GOST.

```powershell
git clone git@github.com:kazza559/tunnel.git
cd tunnel
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m modal setup  # bỏ qua nếu đã đăng nhập
python tunnel.py connect
```

Đợi dòng `SSH ready` trước khi gửi request. Lần đầu có thể mất thời gian build image. Không cần chạy `deploy.py` cho chế độ SSH.

Trong terminal ứng dụng riêng:

```powershell
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
python check_clients.py codex
python check_clients.py claude
```

CLI vẫn dùng login local. Nếu còn custom endpoint/auth override của gateway cũ, xem [hướng dẫn ứng dụng](docs/clients.md).

## Endpoint ngẫu nhiên được xử lý ra sao?

Không cố giữ hostname/port của sandbox. Mỗi lần SSH kết nối lại, client hỏi Modal API để tìm sandbox theo tên; nếu sandbox đã hết hạn, tạo mới rồi lấy `tcp_socket` hiện tại. Cổng local cho ứng dụng vẫn là `7890`.

Host key SSH được tạo và ghim riêng, giữ nguyên qua những lần đổi sandbox. Client dùng `StrictHostKeyChecking=yes` và alias `tunnel-modal`; không dùng accept-new, không quét host key từ endpoint ngẫu nhiên để tự tin nó. Khi mất SSH, request tới domain đã chọn bị từ chối cho đến lúc nối lại, không fallback DIRECT/WSS. Request đang chạy có thể thất bại; chỉ các kết nối mới được phục hồi, không tự phát lại payload.

## Domain filter

[`domains.txt`](domains.txt) chỉ chọn domain OpenAI/ChatGPT và Anthropic/Claude, gồm API/auth và first-party content. Đã đối chiếu hai repo `codex-proxy` và `claude-proxy`. Domain ngoài danh sách đi DIRECT; shared host như GitHub/npm và host kiểm tra IP không được chọn mặc định. Xem [chi tiết filter](docs/routing.md).

## Trạng thái kiểm chứng

- HTTP CONNECT và SOCKS5 qua SSH đã chạy thật trên Modal.
- Đã cố tình xóa sandbox: client tự tìm endpoint TCP mới, giữ host key và khôi phục request.
- OpenSSH thật từ chối một host key ghim sai.
- Codex/Claude đã trả `7` và `Hanoi` qua SSH; xem [kết quả](VERIFICATION.md).

## Lệnh

| Lệnh | Tác dụng |
|---|---|
| `python tunnel.py connect` | SSH mặc định, tự tìm endpoint và nối lại |
| `python tunnel.py connect --port 7891` | Đổi cổng proxy local |
| `python tunnel.py connect --trace` | Ghi hostname/port đích khi chẩn đoán |
| `python tunnel.py stop` | Dừng sandbox SSH được ghi lại và sandbox thử cũ; dừng frontend trước |
| `python -m unittest -v` | Test local, không dùng cloud/model |
| `python check_clients.py codex` | Model smoke qua Codex thật; proxy phải ở cổng 7890 |
| `python check_clients.py claude` | Model smoke qua Claude Code thật; dùng quota tài khoản |
| `python tunnel.py connect-wss` | Chọn rõ chế độ WSS cũ; không được dùng làm fallback tự động |
| `python deploy.py` | Deploy/redeploy riêng chế độ WSS cũ |
| `python tunnel.py start --port 7891 --duration 300 --smoke` | Sandbox TCP/TLS cũ, chỉ dùng để kiểm tra |

Ctrl+C dừng frontend và child SSH. Sandbox có idle timeout 300 giây và lifetime tối đa 24 giờ; một phiên SSH mở giữ sandbox active và có chi phí. Dùng `python tunnel.py stop` sau khi frontend dừng nếu muốn dọn cloud ngay.

## Bật local mới dùng cloud

`python tunnel.py connect` chủ động tìm/tạo sandbox và giữ SSH mở; chưa phải lazy start theo từng request. Khi local còn chạy, sandbox thường vẫn active dù không gửi câu hỏi. Dừng local rồi `python tunnel.py stop` để dọn cloud ngay, hoặc để sandbox hết idle khoảng 5 phút nếu không còn kết nối/hoạt động.

Xem [vòng đời, chi phí và hướng cải tiến](docs/lifecycle.md) để hiểu reuse, keepalive, hết hạn 24 giờ, crash cleanup và những tính năng tối ưu chưa triển khai.

## Tài liệu

- [Vòng đời, chi phí và hướng cải tiến](docs/lifecycle.md)
- [Domain filter, nguồn đối chiếu và cách áp dụng](docs/routing.md)
- [Kiến trúc, DNS và ranh giới tin cậy](docs/architecture.md)
- [Vận hành, endpoint, key, dừng và xử lý lỗi](docs/operations.md)
- [Codex/Claude và loại override cũ](docs/clients.md)
- [Các lượt kiểm chứng thật](VERIFICATION.md)

## Giới hạn

- SSH thuần tới Modal dùng **cổng TCP ngẫu nhiên**, không bảo đảm cổng 443. Nếu mạng A chỉ cho HTTPS qua proxy, chế độ này có thể không chạy. DPI vẫn có thể nhận ra SSH.
- Mạng thấy relay Modal và metadata; agent trên A vẫn có thể thấy loopback/process/file. SSH không bảo vệ khỏi người kiểm soát máy A hoặc control plane Modal.
- Key SSH và truy cập Modal phải được khởi tạo/quản lý qua môi trường tin cậy. `.runtime/` chứa private key, không commit/chia sẻ.
- Chỉ ứng dụng dùng proxy mới đi qua tunnel. Dùng SOCKS hostname (`socks5h`); IP đã resolve trước không có domain để chọn route.
- DIRECT không kế thừa HTTP proxy/PAC công ty. Site nội bộ chỉ đi qua B được vẫn cần đường B riêng.
- TCP/HTTP/HTTPS, không UDP/BIND/TUN; idle của một luồng relay 5 phút sẽ đóng. Chưa kiểm tra trên mạng công ty hoặc chạy chờ hết 24 giờ.

Repo cũ `codex-proxy` không bị sửa. WSS cũ có endpoint `https://kazimhung559--v0z.us-east.modal.run`, vẫn được giữ nhưng không tham gia chế độ SSH.
