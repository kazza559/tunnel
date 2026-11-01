# Vòng đời, chi phí và hướng cải tiến

Tài liệu này mô tả **logic đang có trong code**, cùng các lựa chọn có thể cải tiến sau này. Những mục “chưa làm” là đề xuất, không phải tính năng đã triển khai.

## Mô hình hiện tại: local chủ động quản lý sandbox

Không cần để một SSH sandbox trên Modal chạy liên tục từ trước. Chạy `python tunnel.py connect` là thao tác bật cả frontend local và quá trình tìm/tạo tunnel cloud.

```text
connect trên A
  -> mở proxy local :7890
  -> tìm sandbox cùng cấu hình bằng Modal API
  -> dùng lại nếu còn sống / tạo mới nếu chưa có
  -> chờ sshd ready, lấy hostname:port hiện tại
  -> mở SSH, kiểm tra host key và mở local port forward
  -> SSH ready: request match filter có thể đi cloud

Ctrl+C trên A
  -> đóng frontend + SSH child
  -> sandbox mất kết nối active, có thể đợi idle 300 giây rồi dừng
  -> hoặc python tunnel.py stop để terminate ngay sandbox đã ghi lại
```

**Đây là bật theo phiên dùng local, chưa phải bật theo từng request.** Vừa chạy `connect` là manager bắt đầu mở SSH, ngay cả khi chưa gửi câu hỏi hoặc tất cả request hiện tại đều đi DIRECT. Để terminal frontend chạy cả ngày thì phiên SSH thường giữ sandbox active cả ngày.

## Ba thứ dễ nhầm

| Thành phần | Bản chất | Khi local tắt |
|---|---|---|
| Code/repo và image cache | Code và artifact để dựng lại dịch vụ | Vẫn còn, không đồng nghĩa container SSH đang chạy |
| Named SSH sandbox | Container Linux chạy sshd và GOST | Có thể còn sống tới idle timeout hoặc bị terminate ngay |
| Deployment WSS cũ `v0z` | Web Function có endpoint cố định, dùng riêng bởi `connect-wss` | Deployment vẫn có thể tồn tại; không phải sandbox SSH mặc định |

`deploy.py` chỉ triển khai WSS cũ. Với SSH, `connect` dùng SDK trực tiếp để tạo/tìm sandbox; không cần redeploy `v0z`. Còn một URL trên dashboard không chứng minh SSH đang mở và cũng không chứng minh một container vẫn đang tính compute.

## Bật lần đầu, bật lại và trạng thái sẵn sàng

Lần đầu cần OpenSSH client, login Modal, key trong `.runtime/` và có thể cần build image. Client tạo key riêng nếu chưa có, tính tên sandbox, tra cứu/tạo tài nguyên rồi lấy TCP endpoint. Các lần sau có thể dùng lại key và image cache, hoặc sandbox chưa hết idle; thời gian khởi động vì vậy có thể khác nhau.

Proxy local mở trước khi SSH đã sẵn sàng. Trong thời gian chờ hoặc reconnect:

- Domain match filter trả lỗi nếu cần cloud, không tự đi DIRECT hoặc WSS.
- Domain ngoài filter vẫn có thể đi DIRECT vì không phụ thuộc SSH.
- Dòng `SSH ready` xác nhận SSH và cổng forward local đã lên; không phải bảo đảm mọi API hoặc tài khoản model đều truy cập được.

“Dùng lại sandbox” không có nghĩa dùng lại một URL đã lưu cứng. `ensure_sandbox()` luôn lấy endpoint hiện tại từ Modal; file `ssh-session.json` ghi trạng thái gần nhất để hỗ trợ dọn tài nguyên.

## Idle 5 phút và lifetime 24 giờ là hai đồng hồ khác nhau

| Giá trị trong code | Ý nghĩa |
|---|---|
| `idle_timeout=300` | Sandbox có thể tự dừng sau 5 phút không có hoạt động được Modal tính là active |
| `timeout=86400` | Giới hạn tuổi tối đa của sandbox, kể cả khi vẫn active |
| SSH `ServerAliveInterval=15`, `ServerAliveCountMax=3` | Kiểm tra kết nối SSH khi cần và phát hiện peer không phản hồi; không phải công tắc giảm chi phí |
| Relay read timeout 300 giây | Giới hạn idle của từng luồng dữ liệu do frontend chuyển tiếp; khác sandbox idle |

Theo [tài liệu Modal](https://modal.com/docs/guide/sandboxes#idle-timeouts), sandbox active khi có kết nối TCP mở qua Tunnel, command qua `exec()` đang chạy hoặc stdin đang được ghi. Vì vậy một phiên SSH còn mở vẫn giữ sandbox active dù bạn không gọi model. Lệnh chẩn đoán `sandbox.exec()` cũng có thể ảnh hưởng trạng thái active; “không có request AI” không đồng nghĩa sandbox idle.

Idle timeout là cơ chế thu hồi khi thật sự không còn hoạt động; không nên xem “tôi chưa gửi câu hỏi trong 5 phút” là bảo đảm cloud đã dừng. Mốc tự thu hồi cũng không phải lịch thanh toán chính xác tới từng giây.

## Các trường hợp dừng

| Thao tác | Local/SSH | Sandbox |
|---|---|---|
| Ctrl+C foreground bình thường | Code dọn SSH child và frontend | Có thể còn tới idle 300 giây |
| Dừng local rồi `python tunnel.py stop` | Đã dừng | Terminate sandbox có ID ghi trong state |
| `python tunnel.py stop` khi manager vẫn chạy | Local manager vẫn hoạt động | Bị dừng rồi manager có thể tạo lại để reconnect |
| Kill/đóng cưỡng bức Python | Có thể còn SSH child mồ côi | Nếu SSH child còn kết nối thì chưa idle |
| Mất mạng hoặc máy A sleep/tắt | Kết nối có thể mất, việc phát hiện không tức thời | Có thể đợi kết nối được nhận là đã đóng rồi mới idle |
| Sandbox hết tuổi 24 giờ | SSH có thể đứt, local manager vẫn sống | Manager tìm/tạo sandbox thay thế và nối lại |

Để ngừng trả phí cho phiên thử sớm nhất: dừng **đúng frontend và SSH child của nó**, rồi chạy `python tunnel.py stop`. Không kill mọi `ssh.exe`, vì có thể có phiên GitHub hoặc máy B đang dùng. Xem [lệnh dừng an toàn](operations.md).

`stop` chỉ biết sandbox gần nhất trong state; không phải lệnh dọn toàn bộ tài nguyên trong workspace. Nếu đã đổi filter, key hoặc server code, tên sandbox cấu hình mới khác tên cũ; xem dashboard nếu cần kiểm kê/dọn các sandbox cũ còn sống.

## Endpoint thay đổi được xử lý thế nào?

Tên sandbox hiện có dạng `ssh-egress-<fingerprint>`, được tính từ:

- Domain list sau khi parse.
- Public key client và public host key.
- Nội dung `ssh_server.py`.

Tên là khóa tra cứu, không phải hostname được cấp cho kết nối mạng. Khi sandbox chết/hết hạn, client có thể tạo một sandbox cùng tên nhưng ID và TCP endpoint khác. Client lấy `tcp_socket` mới, còn server host key vẫn giữ nguyên để SSH xác minh danh tính qua alias `tunnel-modal`.

Đã kiểm tra thực tế: terminate sandbox khi frontend còn chạy, client chuyển sang endpoint khác và request mới thành công, host public key không đổi. Đây là kiểm tra cơ chế thay sandbox; chưa chạy soak test chờ tự hết đủ 24 giờ.

SSH process lỗi sẽ làm route cloud chuyển sang chưa sẵn sàng. Manager dọn child cũ, tra cứu lại API, chờ rồi mở SSH mới; delay retry tăng tới tối đa 15 giây. Thời gian phục hồi còn gồm phát hiện mất kết nối, API, build/readiness và SSH handshake; không bảo đảm chỉ mất 15 giây.

Request đang chạy có thể mất kết nối. Manager không giữ lại TCP stream, không tự phát lại prompt/body và không bảo đảm exactly-once. CLI có thể tự retry theo cơ chế riêng; cần tính tới điều đó khi request có tác dụng phụ.

## Chi phí: tiết kiệm ở đâu?

Cấu hình sandbox hiện tại yêu cầu CPU 0.25 core và RAM 128 MiB. Có thể có chi phí compute, egress và các tài nguyên khác theo gói/điều khoản Modal; tài liệu này không ấn định một giá USD vì giá và cách tính có thể thay đổi.

- Nếu chỉ dùng vài phiên/ngày và dừng local khi xong, sandbox không cần được giữ hoạt động 24/7.
- Nếu bật local cả ngày, SSH đang mở giữ sandbox active dù số request ít; không có cơ chế tự giảm compute theo số câu hỏi trong code này.
- Dừng local bình thường vẫn có khoảng chờ idle có thể phát sinh chi phí. Terminate sau khi dừng local tránh phải chờ khoảng đó.
- Reuse sandbox trong khoảng idle có thể giảm chờ khởi động, nhưng đang đánh đổi bằng thời gian tài nguyên còn sống.
- Không khẳng định mô hình này luôn rẻ hơn VPS; kết quả tùy số giờ dùng, lưu lượng, cold start và pricing thực tế.

Tham khảo [Modal pricing](https://modal.com/pricing) và [egress billing](https://modal.com/docs/guide/network-egress-billing) khi ước tính hoặc kiểm tra bill. Không bật sandbox chỉ để xem tài liệu hay trạng thái Git.

## Bản đồ code để cải tiến

| Chỗ trong code | Trách nhiệm cần xem |
|---|---|
| `ssh_tunnel.py:prepare_keys` | Tạo key riêng, quyền file và known_hosts |
| `ssh_tunnel.py:sandbox_name` | Xác định sandbox nào thuộc cùng cấu hình |
| `ssh_tunnel.py:ensure_sandbox` | Lookup/create, image, Secret, timeout/idle, TCP endpoint và state |
| `ssh_tunnel.py:ssh_command` | Host-key checks, keepalive và PermitOpen target của port forward |
| `ssh_tunnel.py:supervise` | Route-ready state, child process và reconnect/backoff |
| `ssh_tunnel.py:connect` | Proxy listener và vòng đời manager |
| `ssh_tunnel.py:stop_sandbox` | Terminate sandbox gần nhất được ghi lại |
| `local_proxy.py:Proxy.connect` | Match route, lỗi cloud không fallback và kết nối DIRECT |
| `test_ssh_tunnel.py` | Endpoint discovery, pin options, filter và fail-closed |

## Hướng cải tiến sau này — chưa triển khai

| Hướng | Lợi ích | Điểm phải giải quyết trước khi làm |
|---|---|---|
| Mở SSH khi có request match đầu tiên | Không giữ cloud chỉ vì frontend đang chạy | Tránh nhiều request cùng tạo sandbox; readiness/timeout phải rõ |
| Đóng SSH sau khi không còn luồng cloud | Giảm compute trong khoảng nghỉ dài | Đếm luồng đang chạy, không ngắt SSE/WSS/response dài; keepalive không tính là request ứng dụng |
| Shutdown thống nhất cho process nền | Dọn đúng frontend/SSH/sandbox bằng một lệnh | IPC/PID identity, tránh kill nhầm, timeout và cleanup khi crash |
| Dọn sandbox ngay khi thoát như một lựa chọn rõ | Giảm chờ idle | Không terminate sandbox còn được client hợp lệ khác dùng |
| Nhiều frontend/máy cùng dùng | Chia sẻ hoặc tách sandbox có chủ đích | Lock file, key ownership, state riêng và quản lý người dùng cuối cùng |
| SSH bọc WSS 443 | Có đường khác khi raw TCP port ngẫu nhiên không đi được | Bridge có xác thực, host-key check riêng, timeout/backpressure; WSS vẫn có thể bị policy chặn |
| Test 24 giờ và lỗi mạng thực tế | Hiểu độ trễ phục hồi, orphan process và bill | Môi trường kiểm tra có kiểm soát, giới hạn chi phí và dọn tài nguyên |
| Health/status không lộ secret | Phân biệt local listening, SSH ready và upstream có auth | Không coi một cổng mở là bằng chứng model hoạt động |

Không thay các giới hạn bảo mật để đổi lấy reconnect: không nhận host key lạ tự động, không hạ kiểm tra host key, không fallback DIRECT cho domain đã chọn và không tự replay request chưa biết kết quả. Tối ưu chi phí không được làm mất response hoặc ngắt luồng còn đang dùng.

Một frontend/runtime directory tại một thời điểm là giới hạn đang được ghi trong code. Code hiện xử lý name conflict khi tạo sandbox, nhưng điều đó không biến toàn bộ local state/key/process management thành hệ thống nhiều client an toàn.
