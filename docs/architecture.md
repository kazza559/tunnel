# Kiến trúc SSH

## Đường dữ liệu

```text
Claude/Codex --HTTP CONNECT hoặc SOCKS5--> Python local :7890
    | domain được chọn
    v
Cổng forward loopback riêng --OpenSSH mã hóa--> sshd trên Modal :2222
    v
GOST 127.0.0.1:8080 --TCP--> OpenAI/Anthropic
```

Python vẫn chọn domain như trước. Với domain đã chọn, nó gửi CONNECT qua cổng forward riêng do `ssh -N -L` mở. CONNECT và HTTPS bên trong được SSH mang qua mạng. Một phiên SSH multiplex nhiều kết nối; không có lớp WSS/TLS ngoài ở đường dữ liệu mặc định.

Modal gọi cổng này là `unencrypted_ports` vì relay không bọc TLS riêng. **Payload vẫn được OpenSSH mã hóa.** HTTP API của ứng dụng tiếp tục được HTTPS riêng bảo vệ. GOST không giải mã HTTPS, không đọc prompt, không đổi payload và không quản token ứng dụng.

## Danh tính và quyền

Lần đầu tạo hai cặp Ed25519 riêng trong `.runtime/`: client key để xác thực tài khoản `tunnel`, server host key để giữ danh tính qua các sandbox. Không dùng private key SSH GitHub hoặc SSH cá nhân khác.

Host public key được đưa vào known_hosts local với alias `tunnel-modal`. OpenSSH kiểm tra `StrictHostKeyChecking=yes`, `IdentitiesOnly=yes`, tắt agent và không tự nhận host key mới. Hostname/port ngẫu nhiên không làm thay đổi danh tính được tin cậy. Pin xuất phát từ key tạo local, không từ `ssh-keyscan` trên kết nối chưa xác minh.

sshd chỉ cho public-key auth, không root/password/TTY/session/shell. Chỉ cho TCP forwarding tới `127.0.0.1:8080`; tắt agent, X11, stream-local forwarding và tun device. GOST chỉ nghe loopback và vẫn kiểm tra allowlist domain/cổng 80/443. GOST không cần password riêng trong chế độ SSH vì key auth và PermitOpen bảo vệ đường vào.

Private host key được gửi vào sandbox qua Modal Secret tạm. Việc cấp key, Modal credentials, image và API điều khiển phải được tin cậy: SSH data plane không bảo vệ được một control plane đã bị chiếm quyền hoặc một máy local đã bị can thiệp. Key local được giới hạn quyền file; trên Windows dùng ACL riêng cho tài khoản hiện tại. Không đưa `.runtime/` lên GitHub.

Chi tiết state machine vận hành và các điểm mở để cải tiến nằm trong [vòng đời và chi phí](lifecycle.md).

## Endpoint discovery và reconnect

Client tính một tên sandbox từ danh sách domain, public key client/host và nội dung entrypoint server. Tên đó là chỉ dẫn tra cứu trong App `tunnel`, không phải địa chỉ TCP. Khi cần kết nối, dùng `Sandbox.from_name`; nếu không còn sandbox sống thì tạo sandbox có cùng tên, chờ ready và gọi `tunnels()[2222].tcp_socket`.

Nếu hai client cùng tạo, bên gặp name conflict tra cứu lại sandbox đã thắng. Thay domain, key hoặc server code tạo tên cấu hình mới; sandbox cũ có thể tự hết idle. Trong một cấu hình, việc recreate vẫn giữ host key đã ghim.

SSH dùng keepalive 15 giây, tối đa 3 lần không phản hồi. Khi process SSH chết, local đánh dấu route cloud chưa sẵn sàng, dọn child cũ, tra cứu API lại rồi mở SSH mới. Retry tăng dần 1 đến 15 giây. Không có fallback sang WSS hoặc DIRECT cho domain được chọn.

Lifetime sandbox tối đa 24 giờ, idle timeout 300 giây. Kết nối SSH đang mở tính là active; việc này không vô hiệu hóa lifetime tối đa. Khi container hết hạn, phiên đang mở có thể đứt; manager phục hồi những kết nối mới, không tiếp tục byte stream hoặc tự gửi lại request cũ.

## Domain và DNS

Domain gốc/subdomain được match theo ranh giới label. `openai.com` khớp `api.openai.com`, không khớp `evilopenai.com`. Domain được chọn ở cổng khác 80/443 vẫn đi vào SSH rồi bị remote từ chối, không rơi về DIRECT.

Hostname đích được GOST phân giải ở Modal. A vẫn cần phân giải relay Modal và dùng Modal API cho discovery. SOCKS client phải gửi hostname (`socks5h`); nếu chỉ gửi IP thì không thể suy ra domain gốc. Repo không sniff SNI hoặc triển khai TUN.

## Ai thấy gì?

| Vị trí | Có thể thấy |
|---|---|
| Thiết bị mạng trên A, không can thiệp endpoint/khóa | Relay host/IP/port, banner SSH, lượng/timing/kết nối; không đọc CONNECT bên trong SSH |
| Agent trên máy A | Có thể thấy process, loopback CONNECT/SOCKS và file; tùy cấu hình/khả năng |
| Modal/sshd/GOST | Destination cần mở; HTTPS ứng dụng vẫn mã hóa khi không có TLS interception được client tin |
| Dịch vụ đích | IP egress cloud của Modal và request HTTPS sau khi dịch vụ giải mã |

CA dùng cho TLS inspection không tự đủ để giải mã SSH được xác minh host key. Điều này không làm SSH vô hình trước DPI và không bảo đảm mạng cho phép TCP port ngẫu nhiên.

## File chính

| File | Trách nhiệm |
|---|---|
| `ssh_tunnel.py` | Key/known_hosts, named sandbox, SSH process, reconnect |
| `ssh_server.py` | Restricted sshd và GOST loopback trong sandbox |
| `local_proxy.py` | HTTP/SOCKS parser, domain routing và chuyển byte |
| `tunnel.py` | CLI và helper dùng chung; giữ sandbox TLS cũ |
| `modal_app.py`, `deploy.py`, `ws_stream.py` | Chế độ WSS cũ được chọn riêng |
| `test_ssh_tunnel.py`, `test_tunnel.py` | Test SSH discovery/fail-closed và các parser/transport cũ |

## Tham khảo

- [Modal raw TCP tunnels](https://modal.com/docs/guide/tunnels#advanced-unencrypted-tcp-tunnels)
- [Modal Sandbox API](https://modal.com/docs/reference/modal.Sandbox)
- [Sandbox lifetime và idle](https://modal.com/docs/guide/sandboxes)
- [OpenSSH client configuration](https://man.openbsd.org/ssh_config.5)
- [OpenSSH server configuration](https://man.openbsd.org/sshd_config.5)
