# Triển khai và vận hành

## Yêu cầu và cấu trúc local

Python 3.11+; `modal` và `websockets` từ `requirements.txt`; tài khoản Modal đã đăng nhập. Sandbox/remote dùng GOST Linux amd64 3.3.2, tải từ GitHub release và xác minh SHA-256. Không cần cài GOST lên Windows.

| Đường dẫn | Nội dung | Commit? |
|---|---|---|
| `.runtime/deployed.json` | URL WSS, domain snapshot, mật khẩu proxy, tên Modal Secret | Không |
| `.runtime/session.json` | ID sandbox theo phiên để dọn tài nguyên | Không |
| `.runtime/session-*/local.json` | Config sandbox tạm, có mật khẩu | Không |
| `.runtime/local.pid` | PID được ghi lại khi chạy nền theo hướng dẫn | Không |
| `.runtime/proxy.*.log` | Log frontend khi chạy nền | Không |
| `.runtime/*-result.json`, `*.stdout.log`, `*.stderr.log` | Kết quả thử CLI | Không |
| `.cache/` | Binary tải về và công cụ local | Không |
| `fixtures/` | Cert/key self-signed cho test localhost | Có; không dùng ngoài test |

## Deployment cố định

```powershell
cd D:\code\tunnel
python -m pip install -r requirements.txt
python -m modal setup  # bỏ qua nếu đã đăng nhập
python deploy.py
```

`deploy.py` thực hiện:

1. Tạo `.runtime/deployed.json` nếu chưa có, với mật khẩu ngẫu nhiên và tên secret riêng.
2. Tạo named Modal Secret; nếu đã tồn tại, giữ secret hiện có.
3. Deploy App `tunnel`, Function `proxy`, endpoint label `v0z`.
4. Lấy URL thực từ Modal; lưu WSS URL với `/ws` và domain snapshot vào config local bằng file tạm rồi replace.

Deployment hiện tại: `https://kazimhung559--v0z.us-east.modal.run`. Workspace khác sẽ có hostname khác: lấy giá trị mà script in ra, không copy hostname workspace hiện tại sang tài khoản khác.

App dùng tối đa một container, CPU 0.25 core, RAM 128 MiB và tối đa 100 concurrent inputs; scale down window 300 giây. Không đặt `min_containers`, nên không giữ container nóng khi không có tải. Một kết nối WSS có thời hạn Function 3600 giây; deployment/URL không hết hạn theo thời gian đó. Có thể có cold start hoặc ngắt kết nối do nền tảng, ứng dụng phải retry/kết nối mới.

Chỉ cần deploy lại khi sửa remote code, `domains.txt` hoặc endpoint label. Chạy `connect` bình thường không gọi deploy và không tạo secret mới.

## Chạy frontend

Foreground, dễ kiểm soát nhất:

```powershell
python tunnel.py connect
# Hoặc:
python tunnel.py connect --port 7891
```

Ctrl+C dừng frontend, không dừng deployment. Khi frontend dừng, ứng dụng đã trỏ `127.0.0.1` sẽ lỗi kết nối; chúng không được frontend tự chuyển sang DIRECT.

Chạy nền trên Windows, trong `D:\code\tunnel`, sau khi chắc chắn cổng 7890 chưa dùng:

```powershell
Get-NetTCPConnection -LocalPort 7890 -State Listen -ErrorAction SilentlyContinue

$tunnelProcess = Start-Process -FilePath (Get-Command python).Source `
  -ArgumentList @('-u', 'D:/code/tunnel/tunnel.py', 'connect') `
  -WorkingDirectory 'D:/code/tunnel' -WindowStyle Hidden `
  -RedirectStandardOutput 'D:/code/tunnel/.runtime/proxy.stdout.log' `
  -RedirectStandardError 'D:/code/tunnel/.runtime/proxy.stderr.log' -PassThru
$tunnelProcess.Id | Set-Content -LiteralPath .runtime/local.pid
```

Đây là tiến trình nền, chưa phải Windows Service hoặc tác vụ tự chạy sau reboot. Đổi đường dẫn trong ví dụ nếu clone ở nơi khác; đường dẫn có dấu cách cần được quote phù hợp trong `ArgumentList`.

Dừng tiến trình do hướng dẫn trên tạo, có kiểm tra PID chưa bị tái sử dụng:

```powershell
$tunnelPid = [int](Get-Content -LiteralPath .runtime/local.pid)
$tunnelInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $tunnelPid"
if ($tunnelInfo.CommandLine -notlike '*D:/code/tunnel/tunnel.py*connect*') {
  throw 'PID không khớp frontend tunnel; kiểm tra lại trước khi dừng'
}
Stop-Process -Id $tunnelPid
```

Dừng cloud deployment riêng:

```powershell
python -m modal app stop tunnel
```

Lệnh này dừng App và các container đang chạy; muốn bật deployment trở lại thì chạy `python deploy.py`. Không nhầm với `python tunnel.py stop`, vốn chỉ dọn sandbox theo phiên.

## Sửa domain, đổi endpoint và redeploy

- Sửa `domains.txt`, mỗi dòng một domain gốc, rồi `python deploy.py` và restart frontend.
- Đổi tên bằng `label="v0z"` tại `@modal.web_server` trong `modal_app.py`; sửa giá trị, deploy lại rồi restart frontend để nạp URL mới.
- Giữ App `tunnel` và Function `proxy` nếu không muốn sửa lookup trong `deploy.py`. Label URL độc lập với hai tên đó.
- Không chỉnh thủ công hostname trong config rồi mong server đã đổi. Script lấy URL thực sau deploy.
- Redeploy có thể cắt các phiên đang mở; thực hiện khi không có request quan trọng. Repo không tự drain phiên cũ.

[Tài liệu Modal về label và URL](https://modal.com/docs/guide/webhook-urls).

## Chuyển máy và sao lưu

Git chỉ chứa code. Để tiếp tục cùng deployment, cài dependency trên máy mới và khôi phục `.runtime/deployed.json` bằng kênh riêng, được bảo vệ và chỉ dành cho tài khoản của bạn. Tạo `.runtime/` nếu chưa có. Sau đó dùng `python tunnel.py connect`; frontend này không cần đăng nhập Modal khi chỉ kết nối tới URL đã có.

File này là credentials proxy: ai có nó có thể dùng egress được allowlist. Không gửi qua issue/chat công khai hoặc commit vào repo, kể cả repo private. Giữ ACL Windows đủ hẹp; POSIX `0600` không thay thế ACL Windows.

Nếu mất config, không coi `python deploy.py` là lệnh chỉ đọc. Script sẽ tạo password/secret mới và redeploy App cùng tên trong workspace, khiến frontend cũ không còn xác thực được. Nếu muốn tạo lại deployment, xác nhận đúng workspace và chấp nhận cập nhật toàn bộ frontend; dọn secret cũ trên Modal sau khi chắc chắn không còn sử dụng. Hiện chưa có lệnh tự khôi phục hoặc rotate password.

## Sandbox theo phiên

Không chạy cùng cổng với frontend deployment đang hoạt động. Dùng cổng riêng khi thử song song:

```powershell
python tunnel.py start --port 7891 --duration 300 --smoke
# Giữ một sandbox foreground trong tối đa 1 giờ:
python tunnel.py start --port 7891
```

`--duration` từ 60 đến 86400 giây, mặc định 3600. Ctrl+C dừng proxy child, terminate sandbox và dọn config tạm. Nếu launcher bị đóng cưỡng bức, `python tunnel.py stop` dùng ID trong `.runtime/session.json` để terminate; lifetime cứng vẫn giới hạn tuổi sandbox. Mỗi phiên có mật khẩu mới. Không tự reconnect hoặc giữ URL sandbox cố định.

Smoke mặc định dùng `api.ipify.org` qua proxy và `example.com` qua DIRECT. Nếu sửa danh sách khiến các host đó đổi route, kết quả smoke cần được diễn giải theo cấu hình mới; không mặc định suy ra proxy hỏng.

## Chẩn đoán

```powershell
python tunnel.py connect --trace
# Với tiến trình nền:
Get-Content .runtime/proxy.stderr.log -Tail 50
Get-Content .runtime/proxy.stdout.log -Tail 50
python -m modal app logs tunnel
```

Không mở thêm frontend cùng cổng để bật trace; dừng frontend hiện tại trước. Trace chỉ in `MODAL hostname:port` sau CONNECT thành công, không phải access log đầy đủ và không phải bằng chứng mọi process trên máy đã dùng proxy. Tắt trace khi hết kiểm tra.

| Triệu chứng | Kiểm tra và xử lý |
|---|---|
| `deployed.json` không tồn tại | Deploy mới hoặc khôi phục config riêng; đọc phần chuyển máy trước khi deploy |
| `Address already in use` / WinError 10048 | Xác định process đang giữ cổng, dùng `--port` khác hoặc dừng đúng process của bạn |
| HTTP 502 ở local | Lỗi TLS/WSS, sai password, remote từ chối domain/cổng hoặc đích không tới được; xem log local/cloud |
| Remote 407 | Thiếu/sai mật khẩu; config local và Modal Secret phải thuộc cùng deployment |
| Remote 403 | Domain/cổng bị allowlist từ chối; không phải lỗi login Codex/Claude |
| API 401 sau CONNECT thành công | Đường mạng đã tới API; kiểm tra login/API key của chính ứng dụng |
| API 403 | Có thể là quyền tài khoản, IP cloud hoặc policy upstream; proxy không cấp quyền truy cập |
| Cổng 7890 mở nhưng CLI vẫn gọi gateway cũ | Gỡ endpoint/provider/auth override trong riêng môi trường thử; xem clients.md |
| SOCKS tới IP bị đi DIRECT | Ứng dụng đã resolve DNS trước; chuyển sang hostname mode/socks5h |
| Website thiếu tài nguyên | Tìm hostname CDN/login còn thiếu; kiểm tra browser có dùng proxy không |
| Domain khác không truy cập được | DIRECT không tự kế thừa proxy/PAC upstream của công ty |
| Windows không thực thi GOST | Frontend local đã dùng Python; không cần tắt Application Control |
| Phiên bị ngắt sau thời gian dài | Xem idle timeout 300 giây và WSS/function timeout 3600 giây; mở request mới |

## Khi push GitHub

`.runtime/`, `.cache/`, `.venv/` và `__pycache__/` không được stage. Kiểm tra bằng `git status --ignored` và `git diff --cached --name-only`. Các key test trong `fixtures/` được commit có chủ đích vì test TLS cần chúng; không dùng chúng làm secret thật.

URL deployment không phải mật khẩu. Không dán giá trị `password` hoặc nội dung file config lên GitHub. Không cần đưa credentials Modal, Codex hoặc Claude vào repository để clone/build code.
