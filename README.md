# claude-autopilot

Bộ kit giúp Claude Code chạy một task lớn từ đầu đến cuối mà không cần người ngồi canh: bạn cùng một model mạnh chốt toàn bộ quyết định thành `SPEC.md` và `tasks.json`, sau đó một script chạy từng task bằng một phiên `claude -p` riêng (context sạch, model và effort riêng), tự kiểm tra, tự commit, tự thử lại và để lại báo cáo cho sáng hôm sau.

## Ý tưởng chính

Hệ thống này chạy tin cậy được qua đêm nhờ ba nguyên tắc.

**Điều phối nằm trong code, không nằm trong Claude.** Thứ tự task, số lần thử, việc nâng model và trạng thái được lưu trong `state.json` và do script Python quản lý. Vì vậy một phiên Claude có crash hay bị giới hạn usage thì cả đêm cũng không hỏng, và chạy lại đúng lệnh cũ sẽ tiếp tục từ chỗ dừng.

**Không tin lời tự báo cáo.** Worker nói "xong" chưa đủ. Script tự chạy các lệnh `verify` (test, typecheck, lint). Với task quan trọng, một phiên Opus chỉ-đọc review diff. Chỉ khi cả hai đều qua thì script mới commit. Nếu fail, worktree được reset về commit tốt gần nhất, lỗi được đưa vào prompt của lần thử sau, và model được nâng lên.

**Không ai hỏi ai.** Worker chạy với `--permission-prompts none`, nên không có công cụ để hỏi bạn. Gặp chỗ mơ hồ thì worker tự chọn theo spec và ghi vào `DECISIONS.md`. Chỉ khi thật sự không thể đi tiếp (spec mâu thuẫn, cần credential, thao tác không thể hoàn tác), worker trả về `blocked` kèm câu hỏi và đề xuất trả lời. Script ghi câu hỏi vào báo cáo, bỏ qua các task phụ thuộc vào nó và vẫn chạy tiếp các nhánh việc khác.

## Cài đặt (một lần cho mọi repo)

```bash
git clone <nơi bạn lưu kit này> ~/.claude-autopilot   # hoặc giải nén vào đó
~/.claude-autopilot/install.sh
```

Lệnh trên tạo lệnh `autopilot` trong `~/.local/bin` và cài skill `/autopilot-plan` vào `~/.claude/skills/`, nên dùng được ở mọi project. Yêu cầu: Claude Code v2.1.259 trở lên (cần cho flag `--permission-prompts`), Python 3.9+, git, và macOS, Linux hoặc WSL.

## Quy trình mỗi task lớn

**1. Lập plan (bạn có mặt, khoảng 20–60 phút).** Trong repo, mở phiên mạnh nhất:

```bash
claude --model claude-opus-5-5 --effort xhigh
> /autopilot-plan Viết lại module billing theo hướng invoice-based, ...
```

Skill sẽ đọc codebase, chạy thử test để chắc baseline đang xanh, rồi hỏi bạn từng nhóm quyết định (luôn kèm phương án đề xuất). Sau đó nó viết `.autopilot/<plan-id>/SPEC.md` và `tasks.json`, gán model và effort cho từng task, chạy `autopilot validate` và `--dry-run` cho bạn xem. Đây là bước duy nhất cần bạn. Thời gian bỏ ra ở đây quyết định chất lượng cả đêm.

**2. Chạy (bạn đi ngủ).** Chạy trong tmux để không chết khi đóng terminal:

```bash
tmux new -s autopilot
autopilot run .autopilot/billing-v2/tasks.json --max-total-usd 60 \
  --notify-cmd 'curl -s -d @- ntfy.sh/<topic-riêng-của-bạn>'
```

Script tạo một git worktree riêng ở `../<repo>.autopilot-<plan-id>` trên branch `autopilot/<plan-id>`, nên checkout chính của bạn không bị đụng tới và bạn vẫn làm việc khác được. Mỗi task thành công là một commit.

**3. Sáng hôm sau.** Đọc `.autopilot/<plan-id>/run/REPORT.md`, có bảng trạng thái, chi phí, và mục "Needs you" liệt kê câu hỏi cùng lỗi cuối. Review bằng `git log` / `git diff main...autopilot/<plan-id>`. Nếu có câu hỏi, bổ sung quyết định vào `SPEC.md` (hoặc sửa `tasks.json`) trong checkout chính, rồi chạy:

```bash
autopilot run .autopilot/billing-v2/tasks.json --retry-failed
```

Hài lòng thì merge hoặc mở PR từ branch đó.

## Chọn model và effort

Model mạnh (lúc lập plan) gán `model` và `effort` cho từng task theo bảng trong skill: Haiku cho việc cơ học đã được đặc tả đầy đủ, Sonnet/medium cho feature thông thường, Sonnet/high cho logic khó nhiều file, Opus/high cho kiến trúc, migration, concurrency, bảo mật, bước tích hợp và review cuối. Nguyên tắc là chọn mức rẻ nhất có khả năng thành công, vì khi thất bại script tự leo thang: lần thử thứ hai lên model kế tiếp (Haiku → Sonnet → Opus, effort tối thiểu `high`); khi đã ở Opus thì tăng effort. Muốn kiểm soát riêng cho một task thì đặt `escalate_to`.

Để kiểm tra trước, `autopilot run ... --dry-run` in ra model và effort của lần đầu và lần retry cho từng task.

## Các trường trong tasks.json

| Trường | Ý nghĩa |
|---|---|
| `defaults.verify` | Lệnh chạy sau mọi task (typecheck, lint, test). Exit khác 0 nghĩa là fail. |
| `defaults.setup` | Chạy một lần khi tạo worktree, ví dụ copy `.env` và `npm ci`. |
| `defaults.permission_mode` | Mặc định `auto` (classifier duyệt từng hành động). Xem phần an toàn bên dưới. |
| `defaults.max_budget_usd` | Trần chi phí mỗi phiên worker, truyền vào `--max-budget-usd`. |
| `defaults.limit_wait_minutes` / `max_limit_waits` | Gặp giới hạn usage thì ngủ rồi `--resume` đúng phiên đó. |
| `task.depends_on` | Task phụ thuộc. Phụ thuộc fail hoặc blocked thì task này bị skip, các nhánh khác vẫn chạy. |
| `task.verify` | Lệnh kiểm tra riêng của task, chạy thêm vào `defaults.verify`. |
| `task.review` | `true` thì một phiên reviewer (mặc định Opus/high, chỉ đọc) phải approve diff. |
| `task.escalate_to` | `{ "model": ..., "effort": ... }` dùng cho các lần thử từ lần 2. |
| `task.context_files` | Gợi ý file liên quan để worker khỏi phải đi tìm, tiết kiệm context. |

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

Trong repo đang làm, mỗi plan nằm ở `.autopilot/<plan-id>/`. Thư mục `run/` (state, log từng phiên dạng JSON, REPORT.md) được tự thêm vào `.git/info/exclude`. `PROGRESS.md` và `DECISIONS.md` nằm trong worktree và đi theo từng commit, để worker sau biết worker trước đã làm gì.
