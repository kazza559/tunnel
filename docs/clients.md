# Codex, Claude Code và kiểm tra kết nối

## Proxy theo tiến trình

Bật frontend trước bằng `python tunnel.py connect`. Trong terminal ứng dụng riêng, đặt các biến trước khi khởi động CLI:

```powershell
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
```

HTTPS_PROXY dùng URL `http://` là đúng: ứng dụng nói HTTP CONNECT tới proxy trên loopback; lớp WSS/TLS tới Modal được frontend quản lý. Không đổi base URL của Codex/Claude thành URL Modal, vì server này không nhận OpenAI/Anthropic API trực tiếp.

Các ví dụ `$env:` chỉ tác động terminal hiện tại và process con, không thay đổi biến User/Machine. Ứng dụng đã mở phải được khởi động lại; browser/desktop/IDE không mặc định kế thừa terminal. Kiểm tra biến lowercase nếu đã cấu hình từ trước vì một số client ưu tiên chúng.

Đối với Codex, môi trường kiểm chứng còn đặt các biến sau để bao phủ các transport của CLI:

```powershell
$env:ALL_PROXY = 'http://127.0.0.1:7890'
$env:WS_PROXY = 'http://127.0.0.1:7890'
$env:WSS_PROXY = 'http://127.0.0.1:7890'
```

Giữ NO_PROXY hẹp. `NO_PROXY=*` hoặc các domain AI trong NO_PROXY có thể làm request không tới frontend.

## Chuyển khỏi gateway cũ

Repo này không sửa `codex-proxy`, cấu hình user hoặc môi trường User/Machine. Vì vậy chỉ đặt biến proxy có thể chưa đủ nếu shell đang giữ OPENAI_BASE_URL trỏ gateway cũ.

Để thử bằng endpoint chính thức, mở terminal riêng và bỏ **trong process đó** những override bạn đã thiết lập cho gateway:

```powershell
$oldEndpointVariables = @(
  'OPENAI_BASE_URL', 'OPENAI_API_BASE', 'ANTHROPIC_BASE_URL',
  'CODEX_REFRESH_TOKEN_URL_OVERRIDE', 'CODEX_REVOKE_TOKEN_URL_OVERRIDE'
)
foreach ($name in $oldEndpointVariables) {
  Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
```

Nếu Codex config có custom `model_provider`, `chatgpt_base_url` hoặc các realtime URL cũ, dùng lượt thử không nạp user config:

```powershell
codex exec --ignore-user-config --ephemeral --sandbox read-only --model gpt-6.1-sol "Reply with exactly: OK"
```

Cờ/model trong ví dụ khớp bản CLI đã kiểm chứng. Nó vẫn dùng login local ở CODEX_HOME; không bỏ yêu cầu auth. Khi chuyển cấu hình dùng hằng ngày, sao lưu config rồi chủ động bỏ các override cũ bạn không cần nữa; repo không tự làm bước đó. Nếu ứng dụng cần proxy/API gateway riêng cho mục đích khác, đừng bỏ override ở cấu hình chung chỉ để chạy thử tunnel.

## Codex CLI

CLI đã kiểm chứng: 0.166.0, model `gpt-6.1-sol`, login ChatGPT có sẵn. Kiểm tra trạng thái login bằng `codex login status`, không cần mở hoặc upload auth.json.

```powershell
python check_clients.py codex
```

Script đặt proxy riêng trong môi trường child, loại endpoint/refresh/revoke override cũ khỏi child, dùng `--ignore-user-config`, `--ephemeral`, read-only và prompt yêu cầu không dùng tools. Nó gọi npm launcher `@openai/codex` nằm cùng bản Node trong PATH. Nếu bạn chỉ cài bản native hoặc cấu trúc cài khác, chạy CLI trực tiếp theo ví dụ thay vì mặc định script hỗ trợ mọi kiểu cài.

Không copy refresh token qua Modal. Codex quản lý login và refresh như bình thường trên local. Phiên model request đã chạy thành công không đồng nghĩa browser login, refresh token và mọi plugin/MCP đã được kiểm chứng.

## Claude Code

CLI đã kiểm chứng: 2.1.316, login Pro có sẵn; model thực tế được trả về trong kết quả kiểm tra là `claude-opus-5-5`. Kiểm tra login bằng `claude auth status`.

```powershell
python check_clients.py claude
# Thử trực tiếp trong terminal đã cấu hình proxy:
claude -p "Reply with exactly: OK"
```

Script dùng CLI native trong PATH, `--safe-mode`, `--restricted`, tắt tools/MCP và không lưu session cho lượt thử. Nó không thực hiện login hoặc tải token lên server. Dùng HTTP CONNECT ở local; tài liệu Claude Code hiện không hỗ trợ SOCKS làm upstream proxy trực tiếp.

[Tài liệu network của Claude Code](https://code.claude.com/docs/en/network-config).

## Script kiểm chứng thực sự làm gì

Cả hai lượt gửi một prompt có hai câu hỏi: “2 + 5?” và “Thủ đô Việt Nam?”. Kết quả cần process exit 0, có `7` và `Hanoi`/`Hà Nội`. Mỗi lượt giới hạn chờ 150 giây và có thể tiêu quota của tài khoản đang đăng nhập.

Frontend phải ở cổng 7890 vì script kiểm chứng hiện dùng cố định cổng này. Script không tự bật proxy, không deploy cloud và không sửa cấu hình user. Phiên bản/cờ/model của CLI có thể cần điều chỉnh nếu dùng máy khác.

| File local | Ý nghĩa |
|---|---|
| `.runtime/codex-result.json` | Exit code, thời gian, câu trả lời và verdict Codex |
| `.runtime/claude-result.json` | Exit code, thời gian, câu trả lời và verdict Claude |
| `.runtime/<client>.stdout.log` | Output CLI gốc để chẩn đoán |
| `.runtime/<client>.stderr.log` | Thông báo lỗi/startup của CLI |

Không gửi nguyên log ra ngoài nếu chưa kiểm tra dữ liệu trong đó. Trong các test đã chạy, chỉ dùng câu hỏi vô hại, không đưa nội dung repo hay dữ liệu nội bộ vào prompt.

## curl, SOCKS và browser

```powershell
curl.exe --silent --show-error --fail --max-time 30 --noproxy "" --proxy http://127.0.0.1:7890 https://api.ipify.org
curl.exe --silent --show-error --fail --max-time 30 --noproxy "" --proxy socks5h://127.0.0.1:7890 https://api.ipify.org
```

`--noproxy ""` tránh NO_PROXY đang có làm curl bỏ qua proxy. `socks5h` yêu cầu truyền hostname tới frontend; domain được chọn sẽ resolve ở Modal. `socks5://` có thể resolve trước trên A, khiến router chỉ thấy IP và chọn DIRECT.

Browser cần cấu hình HTTP proxy hoặc SOCKS v5 + remote DNS riêng. Đảm bảo localhost callback của OAuth không bị đưa ra cloud. Repo chưa có extension, PAC hoặc tự chỉnh Windows system proxy. Các process con/plugin cũng không chắc kế thừa cấu hình nếu được launcher khác tạo.

Nếu site nội bộ chỉ vào được qua B, giữ đường SSH/proxy B riêng cho chúng; bộ router hiện chỉ có Modal và DIRECT. Không thêm domain nội bộ vào Modal với kỳ vọng Modal có quyền truy cập mạng riêng đó.

## Bậc kiểm tra

1. `python -m unittest -v`: không dùng cloud/model; test parsing, domain, policy, TLS/WSS, DNS và lifecycle.
2. curl qua frontend đã triển khai: xác nhận CONNECT/SOCKS có egress thật, chưa xác nhận tài khoản model.
3. `check_clients.py`: xác nhận CLI đã đăng nhập gửi được request model qua cấu hình kiểm chứng.
4. Dùng workflow thực tế: browser login, token refresh, MCP/plugin, phiên dài và mạng công ty vẫn cần kiểm tra riêng.

Xem [VERIFICATION.md](../VERIFICATION.md) để phân biệt rõ những bước đã chạy và những phần chưa kiểm tra.
