# Kiến trúc và phạm vi

## Hai chế độ transport

| Chế độ | Cloud | Transport A → Modal | Vòng đời |
|---|---|---|---|
| `connect` | Web Function triển khai bằng `deploy.py` | WSS tới `/ws` trên endpoint cố định | Deployment tồn tại; container chạy theo nhu cầu |
| `start` | Sandbox tạo theo mỗi lần chạy | TCP/TLS qua `encrypted_ports=[8080]` | Mặc định 1 giờ, tối đa 24 giờ; Ctrl+C dọn sandbox |

Hai chế độ dùng cùng HTTP proxy GOST trên Linux và cùng thuật toán domain ở frontend. Frontend local dùng Python, tránh phụ thuộc binary GOST Windows bị Application Control chặn trên máy phát triển. Không thay đổi chính sách bảo vệ Windows.

## Đường request HTTPS

1. Ứng dụng gửi `CONNECT api.anthropic.com:443` tới `127.0.0.1:7890`, hoặc gửi SOCKS5 CONNECT chứa hostname.
2. Python chuẩn hóa hostname và kiểm tra domain gốc/subdomain trong config local.
3. Với domain được chọn, frontend mở WSS tới Modal, kiểm tra chứng chỉ bằng trust store mặc định, rồi gửi CONNECT có proxy authentication bên trong WSS.
4. GOST xác thực mật khẩu, kiểm tra allowlist domain và cổng 80/443 rồi mở kết nối đích. GOST phân giải DNS đích trên Modal.
5. Frontend trả CONNECT thành công cho ứng dụng và chuyển byte hai chiều. Ứng dụng tự thực hiện TLS tới OpenAI/Anthropic qua đường đó.

Modal kết thúc lớp TLS/WSS ngoài. TLS của ứng dụng tới dịch vụ đích vẫn chạy bên trong, không có TLS MITM do repo này chủ động thực hiện. SSE, HTTP/2 và WSS của ứng dụng được mang dưới dạng byte trong đường CONNECT; không có route xử lý API riêng.

Với HTTP thông thường, frontend nhận absolute URL, chuyển thành origin-form trước khi gửi tới đích và loại proxy auth do client cung cấp. Nhánh remote vẫn dùng CONNECT tới cổng 80.

## Domain, DNS và fail-closed

`domains.txt` chỉ chứa domain gốc, mỗi dòng một tên, cho phép comment `#`. `openai.com` khớp cả `openai.com` và `api.openai.com`, không khớp `evilopenai.com` hay `openai.com.attacker.invalid`. Cấu hình không chấp nhận URL, wildcard, IP hoặc cổng.

Domain đã chọn đi Modal ở mọi cổng; remote chỉ cho 80/443, nên chọn cổng khác sẽ bị từ chối thay vì đi DIRECT. Lỗi WSS, auth hoặc CONNECT cũng không fallback. Domain không được chọn dùng DNS/kết nối TCP trên máy local.

`SOCKS5` có thể gửi hostname hoặc IP. Dùng `socks5h` để proxy nhận hostname. Repo không thực hiện sniff SNI hoặc suy ngược IP thành domain. Domain đích đã chọn không cần resolve local; hostname của chính endpoint Modal vẫn cần được resolve để mở tunnel.

Rule chỉ nhìn hostname, không nhìn path trong HTTPS. Muốn đưa `github.com/openai/...` qua tunnel phải xét host `github.com`; rule “URL chứa openai” không tồn tại ở đây. Browser và plugin có thể dùng CDN/SSO bên ngoài danh sách mặc định; chỉ thêm khi có nhu cầu thực tế.

## Credentials và dữ liệu ở đâu

| Dữ liệu | Nơi lưu |
|---|---|
| Login/API key Codex và Claude | Credential store riêng của ứng dụng trên A |
| Mật khẩu proxy deployment | Named Modal Secret và `.runtime/deployed.json` ở A |
| Mật khẩu proxy sandbox | Secret tạm, config local tạm theo phiên |
| Config GOST trên cloud | File riêng trong `/tmp`, tạo với quyền POSIX `0600` |
| Local CLI test output | `.runtime/*-result.json` và log đi kèm, không commit |
| Chứng chỉ/key test | `fixtures/`, chỉ cho test localhost |

Mật khẩu proxy được tạo ngẫu nhiên; deployment giữ nguyên khi redeploy cùng config, sandbox tạo mới mỗi phiên. `Proxy-Authorization` chỉ dùng giữa frontend và GOST. Credentials dịch vụ đích đi trong TLS của ứng dụng; repo không đọc token, đổi bearer hay refresh thay ứng dụng.

Frontend chỉ bind loopback, nhưng process khác trên cùng máy có thể dùng cổng này mà không có local authentication. Trên Windows, `0600` không thay thế ACL: thư mục runtime phải thuộc phạm vi tài khoản của bạn. Không gửi config runtime vào issue, log công khai hoặc Git.

## Những gì còn quan sát được

Mạng A có thể thấy DNS/SNI hoặc hostname/IP Modal, số kết nối, thời gian và dung lượng. Mỗi kết nối đích có một kết nối WSS riêng, không phải SSH multiplexing một flow. Nếu lớp TLS ngoài bị giải mã bằng CA được máy tin cậy, hostname trong CONNECT và proxy authentication có thể lộ với bên giải mã đó. Phần mềm giám sát trên máy A còn có thể quan sát process, loopback hoặc dữ liệu ở ứng dụng.

Modal/GOST biết destination hostname và byte stream được chuyển tiếp. Với HTTPS, nội dung application vẫn được TLS bảo vệ khi không có interception được client tin cậy. IP egress của Modal có thể bị dịch vụ đích chặn hoặc yêu cầu xác minh.

## Bản đồ code

| File | Trách nhiệm |
|---|---|
| `tunnel.py` | Domain validation, GOST download/checksum, config, sandbox lifecycle và CLI |
| `local_proxy.py` | HTTP/SOCKS handshake, chọn route, CONNECT upstream và chuyển tiếp TCP |
| `ws_stream.py` | Đổi binary WebSocket message thành giao diện byte stream |
| `modal_app.py` | Web Function label `v0z`, GOST WebSocket listener và tài nguyên cloud |
| `deploy.py` | Secret, Modal deploy, lấy URL và lưu config local |
| `smoke.py` | Kiểm tra policy/IP và HTTPS transport của sandbox |
| `check_clients.py` | Spawn Codex/Claude thật với môi trường proxy riêng |
| `test_tunnel.py` | Unit/lifecycle test và socket integration test cho TLS/WSS |

GOST Linux được pin bản 3.3.2; checksum SHA-256 của archive được kiểm tra khi tải mới. Cache là tài sản local được tin cậy; không tải lại/rehash binary đã tồn tại mỗi lần chạy.

## Nguồn tham khảo

- [Modal WebSockets](https://modal.com/docs/guide/webhooks#websockets)
- [Modal TCP tunnels](https://modal.com/docs/guide/tunnels)
- [Modal Sandbox networking](https://modal.com/docs/guide/sandbox-networking)
- [GOST HTTP proxy](https://gost.run/en/tutorials/protocols/http/)
- [GOST domain rules](https://gost.run/en/concepts/bypass/)
- [GOST 3.3.2 release](https://github.com/go-gost/gost/releases/tag/v3.3.2)
