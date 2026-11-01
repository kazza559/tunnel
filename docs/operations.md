# Vận hành SSH

## Khởi động

```powershell
cd D:\code\tunnel
python -m pip install -r requirements.txt
ssh -V
python -m modal setup  # nếu chưa có login Modal
python tunnel.py connect
```

Chờ `SSH ready`. Client cần Modal credentials cho discovery/recreate; lần đầu có thể build image `openssh-server` và GOST Linux. Không cần chạy `deploy.py` hoặc gọi endpoint WSS.

Dùng `--port 7891` để đổi cổng local, `--trace` để ghi domain đích khi chẩn đoán. Chỉ chạy một frontend cho cùng bộ `.runtime/` tại một thời điểm; các file state/log không thiết kế cho nhiều manager song song.

## Các file riêng tư

| File/thư mục | Nội dung |
|---|---|
| `.runtime/ssh_client_ed25519` và `.pub` | Key đăng nhập SSH riêng của tunnel |
| `.runtime/ssh_host_ed25519` và `.pub` | Host identity riêng, giữ ổn định giữa các sandbox |
| `.runtime/ssh_known_hosts` | Public host key được ghim dưới alias `tunnel-modal` |
| `.runtime/ssh_config` | Config trống để không kế thừa tùy chỉnh SSH user |
| `.runtime/ssh-session.json` | Sandbox ID/name và TCP endpoint vừa được tra cứu |
| `.runtime/ssh.stderr.log` | Thông báo lỗi OpenSSH; không ghi payload |
| `.runtime/deployed.json` | Credentials riêng cho WSS cũ, không dùng cho SSH |
| `.cache/` | Binary tải về; không commit |

Không chỉnh `ssh-session.json` để đổi server, vì endpoint được tra cứu lại từ Modal API. Không xóa known_hosts để bỏ qua cảnh báo sai host key. Chuẩn bị key và quyền quản lý Modal từ môi trường tin cậy; cơ chế SSH không thay thế bảo vệ máy/control plane.

## Dừng

Foreground: Ctrl+C dừng frontend và SSH child. Sandbox có thể tiếp tục sống tới khi idle 300 giây; một SSH mở giữ sandbox active và phát sinh compute/egress.

Dừng cloud ngay sau khi frontend đã dừng:

```powershell
python tunnel.py stop
```

Nếu gọi lệnh này khi frontend còn chạy, manager sẽ tạo lại sandbox để phục hồi kết nối. Vì vậy thứ tự là **dừng local trước, dừng sandbox sau**. Lệnh cũng dọn sandbox TCP/TLS cũ nếu có state cũ.

## Chạy nền Windows

Trong `D:\code\tunnel`, kiểm tra cổng trước rồi tạo process ẩn:

```powershell
Get-NetTCPConnection -LocalPort 7890 -State Listen -ErrorAction SilentlyContinue
$tunnelProcess = Start-Process -FilePath (Get-Command python).Source `
  -ArgumentList @('-u', 'D:/code/tunnel/tunnel.py', 'connect') `
  -WorkingDirectory 'D:/code/tunnel' -WindowStyle Hidden `
  -RedirectStandardOutput 'D:/code/tunnel/.runtime/proxy.stdout.log' `
  -RedirectStandardError 'D:/code/tunnel/.runtime/proxy.stderr.log' -PassThru
$tunnelProcess.Id | Set-Content -LiteralPath .runtime/local.pid
```

Đường dẫn có dấu cách cần quote phù hợp trong `ArgumentList`. Đây chưa phải Windows Service hoặc tác vụ tự chạy sau reboot. Với process chạy nền, Ctrl+C không áp dụng; khi dừng cưỡng bức, phải dừng cả SSH child do frontend tạo. Kiểm tra PID/command line trước, không kill mọi `ssh.exe` vì có thể có phiên GitHub/B của bạn.

```powershell
$tunnelPid = [int](Get-Content -LiteralPath .runtime/local.pid)
$tunnelInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $tunnelPid"
if ($tunnelInfo.CommandLine -notlike '*D:/code/tunnel/tunnel.py*connect*') {
  throw 'PID không khớp tunnel; kiểm tra trước khi dừng'
}
$sshChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $tunnelPid" |
  Where-Object { $_.Name -eq 'ssh.exe' -and $_.CommandLine -like '*HostKeyAlias=tunnel-modal*' }
Stop-Process -Id $tunnelPid
$sshChildren | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
python tunnel.py stop
```

Không dùng `modal app stop tunnel` để chỉ dừng một sandbox nếu còn cần WSS cũ hoặc tài nguyên khác trong App; lệnh đó dừng cả App.

## Endpoint đổi hoặc sandbox hết hạn

Không cần sửa hostname/port trong config. Manager tự lấy endpoint ở mỗi lần nối lại. Sandbox có lifetime tối đa 86400 giây và idle 300 giây. Khi phiên đứt, request đang chạy có thể lỗi; sau dòng `SSH ready` mới, client ứng dụng cần mở kết nối mới.

Tên sandbox có fingerprint của domain, public identity và server source; thay những phần đó cần restart frontend để nạp cấu hình. Nếu thay domain hoặc server code, sandbox cấu hình cũ có thể còn sống tới khi idle. Kiểm tra dashboard nếu cần dọn ngay các phiên cũ.

Host key không được tự thay khi endpoint đổi. Thông báo `REMOTE HOST IDENTIFICATION HAS CHANGED` là lỗi xác minh cần điều tra, không phải lý do để dùng `StrictHostKeyChecking=no`.

## Chuyển máy / mất key

Không có private key trên GitHub. Giữ cả client/host key và public key tương ứng trong bản sao riêng được bảo vệ. Khi chuyển sang máy của chính bạn, khôi phục vào `.runtime/`, bảo đảm ACL/quyền file, login Modal đúng workspace rồi chạy `connect`.

Nếu tạo lại key, đó là một identity mới; tên sandbox cũng đổi. Không thể dùng key mới để vào sandbox cũ. Không lấy host key trên mạng chưa xác minh làm pin. Hiện không có dịch vụ cấp phát hoặc quy trình rotate key tự động.

## Chẩn đoán

```powershell
Get-Content .runtime/ssh.stderr.log -Tail 50
Get-Content .runtime/proxy.stdout.log -Tail 50
Get-Content .runtime/proxy.stderr.log -Tail 50
```

| Triệu chứng | Xử lý |
|---|---|
| `waiting for SSH` kéo dài | Kiểm tra login/quyền Modal, build image và kết nối raw TCP ra relay |
| `Connection timed out` tới cổng ngẫu nhiên | Mạng có thể không cho SSH/TCP port đó; SSH không tự giả thành HTTPS 443 |
| `Permission denied (publickey)` | Kiểm tra đúng bộ client key và sandbox identity; không chuyển sang password auth |
| Sai host key | Dừng và xác minh bootstrap/control plane/key local; không tự nhận key mới |
| HTTP 502 khi reconnect | Route cloud đang chưa sẵn sàng hoặc remote từ chối; chờ `SSH ready`, thử request mới |
| HTTP 403 ở proxy remote | Domain/cổng không nằm trong allowlist 80/443 |
| API 401 sau tunnel thành công | Kiểm tra login/API key ứng dụng, không phải lỗi endpoint discovery |
| DIRECT không truy cập được | Không tự kế thừa proxy/PAC công ty; B vẫn cần cho site nội bộ riêng |
| Cổng 7890 đang dùng | Dừng đúng frontend cũ hoặc đổi `--port` |

## WSS / sandbox TLS cũ

WSS cũ vẫn có tại `https://kazimhung559--v0z.us-east.modal.run/ws` (dùng scheme `wss://` trong config). Chỉ chọn bằng `python tunnel.py connect-wss`; cần `.runtime/deployed.json`. `python deploy.py` chỉ deploy chế độ này. SSH không tự chuyển sang WSS khi lỗi.

Sandbox TLS cũ: `python tunnel.py start --port 7891 --duration 300 --smoke`. Không chạy trùng cổng local. Chế độ smoke thêm `api.ipify.org` riêng cho phiên kiểm tra; filter production không chứa host này. Tài liệu lịch sử và kết quả WSS được giữ trong `VERIFICATION.md`.
