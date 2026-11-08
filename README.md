# claude-autopilot

Bộ kit giúp Claude Code chạy một task lớn từ đầu đến cuối mà không cần người ngồi canh: bạn cùng một model mạnh chốt toàn bộ quyết định thành `SPEC.md` và `tasks.json`, sau đó một script chạy từng task bằng một phiên `claude -p` riêng (context sạch, model và effort riêng), tự kiểm tra, tự commit, tự thử lại và để lại báo cáo cho sáng hôm sau.

## Ý tưởng chính

Hệ thống này chạy tin cậy được qua đêm nhờ ba nguyên tắc.

**Điều phối nằm trong code, không nằm trong Claude.** Thứ tự task, số lần thử, việc nâng model và trạng thái được lưu trong `state.json` và do script Python quản lý. Vì vậy một phiên Claude có crash hay bị giới hạn usage thì cả đêm cũng không hỏng, và chạy lại đúng lệnh cũ sẽ tiếp tục từ chỗ dừng.

**Không tin lời tự báo cáo.** Worker nói "xong" chưa đủ. Script tự chạy các lệnh `verify` (test, typecheck, lint). Với task quan trọng, một phiên Opus chỉ-đọc review diff. Chỉ khi cả hai đều qua thì script mới commit. Nếu fail, script trước hết đưa lỗi trở lại **chính phiên worker đó** (`--resume`, context và code dở đã có sẵn nên rẻ và nhanh) để sửa một vòng; chỉ khi vòng sửa đó vẫn fail thì worktree mới được reset về commit tốt gần nhất, lỗi được đưa vào prompt của lần thử sau, và model được nâng lên.

**Không ai hỏi ai.** Worker chạy với `--permission-prompts none`, nên không có công cụ để hỏi bạn. Gặp chỗ mơ hồ thì worker tự chọn theo spec và ghi vào `DECISIONS.md`. Chỉ khi thật sự không thể đi tiếp (spec mâu thuẫn, cần credential, thao tác không thể hoàn tác), worker trả về `blocked` kèm câu hỏi và đề xuất trả lời. Script ghi câu hỏi vào báo cáo, bỏ qua các task phụ thuộc vào nó và vẫn chạy tiếp các nhánh việc khác.

## Cài đặt (một lần cho mọi repo)

```bash
git clone <nơi bạn lưu kit này> ~/.claude-autopilot   # hoặc giải nén vào đó
~/.claude-autopilot/install.sh
```

Lệnh trên tạo lệnh `autopilot` trong `~/.local/bin` và cài skill `/autopilot-plan` vào `~/.claude/skills/`, nên dùng được ở mọi project. Yêu cầu: Claude Code v2.1.259 trở lên (cần cho flag `--permission-prompts`), Python 3.9+, git, và macOS, Linux hoặc WSL.

Cài thêm [herdr](https://herdr.dev) để chạy qua đêm (khuyến nghị):

```bash
brew install herdr                     # hoặc: curl -fsSL https://herdr.dev/install.sh | sh
herdr integration install claude       # để herdr hiện trạng thái phiên Claude lập plan
```

Mặc định herdr không bật popup thông báo. Muốn sáng dậy thấy thông báo, thêm vào `~/.config/herdr/config.toml` rồi chạy `herdr server reload-config`:

```toml
[ui.toast]
delivery = "system"   # hoặc "herdr" để hiện toast trong giao diện herdr
```

## Quy trình mỗi task lớn

**1. Lập plan (bạn có mặt, khoảng 20–60 phút).** Mở herdr ở repo, rồi mở phiên mạnh nhất trong một pane:

```bash
cd ~/code/myrepo && herdr
claude --model claude-opus-5-5 --effort xhigh
> /autopilot-plan Viết lại module billing theo hướng invoice-based, ...
```

Skill sẽ đọc codebase, chạy thử test để chắc baseline đang xanh, rồi hỏi bạn từng nhóm quyết định (luôn kèm phương án đề xuất). Sau đó nó viết `.autopilot/<plan-id>/SPEC.md` và `tasks.json`, gán model và effort cho từng task, chạy `autopilot validate` và `--dry-run` cho bạn xem. Đây là bước duy nhất cần bạn. Thời gian bỏ ra ở đây quyết định chất lượng cả đêm.

**2. Chạy (bạn đi ngủ).** Cuối phiên lập plan, skill sẽ đưa lệnh này (và hỏi có muốn nó chạy giúp không):

```bash
autopilot herdr .autopilot/billing-v2/tasks.json --max-total-usd 60
```

Lệnh này tạo một workspace herdr tên `autopilot billing-v2`, chạy `autopilot run` trong đó rồi trả về ngay. Bạn có thể đóng terminal, gập laptop hay mất SSH: run vẫn sống trong herdr server. Nếu bạn đang ở trong một pane herdr, gõ thẳng `autopilot run …` ở pane đó cũng được.

Script tạo một git worktree riêng ở `../<repo>.autopilot-<plan-id>` trên branch `autopilot/<plan-id>`, nên checkout chính của bạn không bị đụng tới và bạn vẫn làm việc khác được. Mỗi task thành công là một commit.

**3. Sáng hôm sau.** Mở `herdr`. Sidebar cho biết ngay kết quả: workspace autopilot hiện **done** nếu mọi task xong, hoặc **blocked** nếu có task cần bạn. Pane autopilot vẫn mở, giữ nguyên log và đường dẫn tới báo cáo; nhấn Enter khi xem xong. Đọc `.autopilot/<plan-id>/run/REPORT.md`, có bảng trạng thái, chi phí, và mục "Needs you" liệt kê câu hỏi cùng lỗi cuối. Review bằng `git log` / `git diff main...autopilot/<plan-id>`. Nếu có câu hỏi, bổ sung quyết định vào `SPEC.md` (hoặc sửa `tasks.json`) trong checkout chính, rồi chạy:

```bash
autopilot run .autopilot/billing-v2/tasks.json --retry-failed
```

Hài lòng thì merge hoặc mở PR từ branch đó.

## Chạy với herdr

Khi `autopilot run` chạy trong một pane herdr (`HERDR_ENV=1`), nó tự làm thêm mấy việc sau, không cần cấu hình.

**Báo trạng thái lên sidebar.** Pane được báo là `working` kèm dòng mô tả như "T03 Invoice service · attempt 2 · sonnet-5-5/high", và tiêu đề pane hiện tiến độ kiểu "3/7 done · $4.20". Khi kết thúc, pane chuyển sang `done`, hoặc `blocked` nếu có task fail hay đang chờ câu hỏi; herdr phát âm thanh và thông báo tương ứng. Vì là trạng thái agent thật, bạn cũng có thể đợi nó từ script: `herdr agent wait <pane-id> --until idle --until blocked`.

**Xem trực tiếp worker đang làm gì.** Mỗi worker chạy với `--output-format stream-json`, nên pane autopilot in ra từng bước theo thời gian thực (💬 suy nghĩ, 🔧 công cụ và file đang dùng, ↳ cho subagent). Các dòng này cũng được ghi vào `run/live.log`. Muốn liếc nhanh mà không mở giao diện: `herdr pane read <pane-id> --source recent --lines 40`.

**Tự chạy lại sau khi herdr server khởi động lại.** Autopilot đăng ký với herdr lệnh resume của chính nó. Sau khi herdr server restart, herdr mở lại pane và chạy lại `autopilot run …`; state.json giúp nó tiếp tục từ task đang dở, và phần thay đổi chưa commit của lần thử bị gián đoạn sẽ được bỏ đi. Lưu ý: herdr khôi phục layout và chạy lại lệnh, nhưng tiến trình cũ không sống sót qua restart, và nếu máy khởi động lại thì bạn cần mở lại herdr server (`herdr` hoặc `herdr server`).

**Giữ pane mở ở cuối** để trạng thái done/blocked còn nguyên đến khi bạn xem. Dùng `--no-hold` nếu muốn script thoát luôn.

**Không để hook của herdr làm nhiễu.** Nếu bạn đã cài `herdr integration install claude`, hook đó sẽ chạy trong mọi phiên Claude Code. Autopilot xoá các biến `HERDR_*` khỏi môi trường của worker, nên các phiên `claude -p` không ghi đè trạng thái pane.

**Chạy trên máy khác.** Với máy dev hoặc server qua SSH, cài herdr và kit ở máy đó, mở bằng `herdr --remote <máy>`, rồi chạy `autopilot herdr …` ở đó. Từ laptop, xem trạng thái bằng `herdr --machine <tên> agent list`.

## Chọn model và effort

Model mạnh (lúc lập plan) gán `model` và `effort` cho từng task theo bảng trong skill: Haiku cho việc cơ học đã được đặc tả đầy đủ, Sonnet/medium cho feature thông thường, Sonnet/high cho logic khó nhiều file, Opus/high cho kiến trúc, migration, concurrency, bảo mật, bước tích hợp và review cuối. Nguyên tắc là chọn mức rẻ nhất có khả năng thành công, vì khi thất bại script tự leo thang theo hai bậc. Bậc một là **vòng sửa (fix-up)**: lỗi verify, ý kiến của reviewer hay lỗi commit hook được gửi lại cho đúng phiên worker vừa làm, với cùng model và thêm một khoản ngân sách nhỏ (`fixup_budget_usd`); phiên đó đã có context và code dở nên thường sửa xong trong vài phút. Bậc hai, chỉ khi vòng sửa vẫn fail: reset worktree và chạy lần thử mới với model kế tiếp (Haiku → Sonnet → Opus, effort tối thiểu `high`); khi đã ở Opus thì tăng effort. Muốn kiểm soát riêng cho một task thì đặt `escalate_to`, hoặc `fixups: 0` để bỏ bậc một.

Để kiểm tra trước, `autopilot run ... --dry-run` in ra model và effort của lần đầu và lần retry cho từng task.

### Những gì script làm để tiết kiệm thời gian và chi phí

- **Vòng sửa trước khi leo thang** (ở trên). Một task Sonnet gần xong mà fail một test không còn bị vứt đi để chạy lại từ đầu bằng Opus.
- **Spec được dán thẳng vào prompt.** Nếu `SPEC.md` không quá `inline_spec_kb` (mặc định 48 KB), worker nhận SPEC, đuôi của PROGRESS.md và DECISIONS.md ngay trong task message, bớt ba lượt gọi tool đọc file ở đầu mỗi task.
- **Reviewer không nuốt cả diff.** Diff dán vào prompt reviewer bị cắt ở `review_inline_diff_kb` (mặc định 120 KB); phần còn lại reviewer tự lấy bằng `git diff` cho từng file theo diff stat, thay vì đổ hàng trăm KB vào Opus.
- **Chờ giới hạn usage đúng bằng thời gian cần chờ.** Nếu thông báo giới hạn có ghi thời điểm reset ("resets at 3am", "try again in 25 minutes") thì script ngủ đúng chừng đó (cộng một phút, tối đa 6 giờ) thay vì cố định `limit_wait_minutes`. Hết `max_limit_waits` mà vẫn bị giới hạn thì run dừng và giữ task ở trạng thái pending, không đốt một lần thử rồi leo lên Opus vì lý do không phải lỗi code.
- Báo cáo và `autopilot status --sessions` có thêm cột thời gian và số vòng sửa của từng task, để bạn thấy task nào ngốn thời gian và điều chỉnh model cho lần plan sau.

## Các trường trong tasks.json

| Trường | Ý nghĩa |
|---|---|
| `defaults.verify` | Lệnh chạy sau mọi task (typecheck, lint, test). Exit khác 0 nghĩa là fail. |
| `defaults.setup` | Chạy một lần khi tạo worktree, ví dụ copy `.env` và `npm ci`. |
| `defaults.permission_mode` | Mặc định `auto` (classifier duyệt từng hành động). Xem phần an toàn bên dưới. |
| `defaults.max_budget_usd` | Trần chi phí mỗi phiên worker, truyền vào `--max-budget-usd`. |
| `defaults.limit_wait_minutes` / `max_limit_waits` | Gặp giới hạn usage thì ngủ rồi `--resume` đúng phiên đó. Nếu thông báo có ghi giờ reset thì ngủ đúng chừng đó; `limit_wait_minutes` chỉ là mức dự phòng. Hết `max_limit_waits` thì run dừng, task giữ pending. |
| `defaults.fixups` / `fixup_budget_usd` / `fixup_timeout_minutes` | Số vòng sửa trong cùng phiên worker trước khi reset và leo thang (mặc định 1, thêm $4 và 30 phút mỗi vòng). `task.fixups` ghi đè cho từng task; `0` để tắt. |
| `defaults.inline_spec_kb` | Dán SPEC.md (và đuôi PROGRESS/DECISIONS) vào prompt khi SPEC không quá kích thước này (mặc định 48). `0` để worker luôn tự đọc file. |
| `defaults.review_inline_diff_kb` | Cắt diff dán cho reviewer ở kích thước này (mặc định 120); reviewer dùng `git diff` cho phần còn lại. |
| `task.depends_on` | Task phụ thuộc. Phụ thuộc fail hoặc blocked thì task này bị skip, các nhánh khác vẫn chạy. |
| `task.verify` | Lệnh kiểm tra riêng của task, chạy thêm vào `defaults.verify`. |
| `task.review` | `true` thì một phiên reviewer (mặc định Opus/high, chỉ đọc) phải approve diff. |
| `task.escalate_to` | `{ "model": ..., "effort": ... }` dùng cho các lần thử từ lần 2. |
| `task.context_files` | Gợi ý file liên quan để worker khỏi phải đi tìm, tiết kiệm context. |
| `defaults.stream` | Mặc định `true`: dùng `stream-json` để có live view. Đặt `false` để quay về `--output-format json` nếu bản Claude Code của bạn có vấn đề với stream. |

Ví dụ đầy đủ ở `examples/tasks.example.json`.

## An toàn

Worker bị cấm `git push`, `commit`, `reset`, `rebase`, `merge`, đổi branch. Chỉ script mới commit, và chỉ trong worktree riêng. Mặc định dùng `auto` permission mode, nghĩa là một classifier xem xét từng lệnh thay cho bạn và chặn những gì rủi ro. Nếu muốn dùng `bypassPermissions` để không bao giờ bị từ chối, chỉ nên làm vậy trong devcontainer, Docker hay VM không có credential production. Đừng dùng nó trên máy chính. Nên thêm deny rule trong `~/.claude/settings.json` cho những thứ bạn không bao giờ muốn chạy tự động (deploy, lệnh database production, v.v.).

`--max-total-usd` dừng cả run khi tổng chi phí ước tính vượt ngưỡng. Con số chi phí là ước tính phía client nên có thể lệch hóa đơn thật. Nếu dùng gói subscription, tác vụ cả đêm sẽ ăn vào giới hạn phiên và giới hạn tuần của bạn; script sẽ ngủ chờ reset rồi chạy tiếp, nhưng giới hạn tuần thì có thể không reset trong đêm.

## Giới hạn và khi nào dùng tính năng có sẵn

Kit chạy các task **tuần tự**. Đây là lựa chọn có chủ ý: mỗi task được xây trên commit đã được kiểm chứng của task trước, không có xung đột merge, và qua một đêm thì thông lượng tuần tự thường đã đủ. Bên trong một task, worker vẫn tự dùng subagent để song song hóa việc tìm kiếm.

Claude Code cũng có sẵn vài cách điều phối nhiều agent, phù hợp cho những trường hợp khác:

- **Dynamic workflows** (`/workflows`, từ khóa `ultracode`) hợp với việc fan-out nhiều agent giống nhau, như audit hàng trăm file hay migrate từng file độc lập, ngay trong một phiên tương tác.
- **Projects** trên claude.ai/code hợp khi bạn muốn việc chạy trên cloud ngay cả khi tắt máy.
- **Agent teams** (thử nghiệm, tắt mặc định) hợp khi muốn một lead điều phối các phiên nói chuyện với nhau.

Kit này phù hợp nhất cho một chuỗi task có thứ tự, chạy trên máy hoặc server của bạn, có cổng kiểm chứng giữa các bước.

## Cấu trúc file

```
autopilot.py                 # orchestrator (chỉ dùng thư viện chuẩn)
prompts/worker.md            # luật cho worker, nạp qua --append-system-prompt-file
prompts/reviewer.md          # luật cho reviewer
skills/autopilot-plan/       # skill /autopilot-plan cho phiên lập plan
examples/tasks.example.json
install.sh
```

Trong repo đang làm, mỗi plan nằm ở `.autopilot/<plan-id>/`. Thư mục `run/` (state, log từng phiên dạng JSONL, `live.log`, REPORT.md) được tự thêm vào `.git/info/exclude`. `PROGRESS.md` và `DECISIONS.md` nằm trong worktree và đi theo từng commit, để worker sau biết worker trước đã làm gì.
