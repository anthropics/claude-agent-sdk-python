---
date: 2026-03-21
type: decision
tags: [context7, research, tools, claude-agent-sdk]
status: active
---

# Decision: Dùng Context7 MCP cho nghiên cứu claude-agent-sdk

## Bối cảnh
Cần nghiên cứu repo claude-agent-sdk-python. Có sẵn README local nhưng cần thêm official docs, demo apps, full API reference.

## Các lựa chọn
1. Chỉ đọc code local + README — đủ nhưng thiếu official docs updates
2. Dùng Context7 MCP — fetch docs online realtime, 988+ snippets
3. Manual web search — chậm, không structured

## Quyết định
**Chọn:** Context7 MCP làm nguồn bổ sung chính cho 4/10 tasks

**Lý do:**
- Verified: 4 sources có sẵn, tổng 2205 code snippets
- Official platform docs (988 snippets, score 86.5) — mới nhất
- Demo apps repo (345 snippets) — không có local
- ClaudeAgentOptions có 40+ fields mà README chỉ show 5

## Sources đã verify
| Source | Library ID | Snippets | Score |
|--------|-----------|----------|-------|
| Platform docs | /websites/platform_claude_en_agent-sdk | 988 | 86.5 |
| SDK docs | /nothflare/claude-agent-sdk-docs | 821 | 83.0 |
| Demos | /anthropics/claude-agent-sdk-demos | 345 | 77.6 |
| GitHub source | /anthropics/claude-agent-sdk-python | 51 | 77.8 |

## Áp dụng cho tasks
- claudeagentsdk-3ma: fetch full API reference
- claudeagentsdk-d0g: verify code flows vs docs
- claudeagentsdk-qw0: fetch demo app patterns
- claudeagentsdk-2e7: compile as primary learning resources
