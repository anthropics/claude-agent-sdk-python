---
name: "Nghien cuu Repository"
description: "Mau 3 giai doan de nghien cuu bat ky repository nao: Discovery > Documentation > Luu tru & Quan ly"
type: tao
tags: [research, repo, learning, feynman, diagram, notion]
created: 2026-03-21
updated: 2026-03-21
used_count: 0
---

# Mau: Nghien cuu Repository

## Mo ta
Dung khi can nghien cuu sau mot repository moi. Bao gom 3 giai doan: kham pha code, tai lieu hoa bang diagram, va luu tru ket qua len Notion/Trello. Phu hop cho bat ky repo nao (framework, library, tool).

## Tasks

### Task 1: Phan tich & Kham pha (Discovery)
- **Type:** task
- **Priority:** P1
- **Estimate:** 90 phut
- **Description:** |
  Nghien cuu repository {repo_name} - Giai doan Discovery:
  1. Doc cac file .md huong dan (README, CONTRIBUTING, docs/) de nam bat tong quan.
  2. Liet ke cau truc thu muc va cac luong code/du lieu chinh.
  3. Su dung /youtube skill de tim kiem cac video huong dan setup hoac best practices lien quan den cong nghe trong repo nay.
  4. Ghi nhan: tech stack, entry points, config files, dependencies chinh.
  5. Luu ket qua vao self-explores/tasks/{task-id}.md
- **Dependencies:** none

### Task 2: Thiet ke & Tai lieu hoa (Documentation)
- **Type:** task
- **Priority:** P1
- **Estimate:** 120 phut
- **Description:** |
  Nghien cuu repository {repo_name} - Giai doan Documentation:
  1. Nghien cuu usecase va chien luoc su dung hieu qua.
  2. Ve cac diagram bang code Mermaid:
     - System Overview Diagram (SUD) — kien truc tong the
     - Software Architecture Diagram (SAD) — cac layer/module
     - Sequence Diagram — luong xu ly chinh
     - Usecase Diagram — cac actor va hanh dong
  3. Neu luong qua lon, chia nho thanh cac module logic.
  4. Moi diagram luu thanh file rieng trong self-explores/tasks/{task-id}-diagram-{N}.md
  5. Tong hop key findings va architecture decisions.
- **Dependencies:** [Task 1]

### Task 3: Luu tru & Quan ly
- **Type:** task
- **Priority:** P2
- **Estimate:** 60 phut
- **Description:** |
  Nghien cuu repository {repo_name} - Giai doan Luu tru:
  1. Tong hop toan bo ket qua vao folder self-explores/:
     - context/{repo_name}.md — tong quan architecture
     - learnings/ — cac dieu hoc duoc
     - decisions/ — cac quyet dinh ve cach su dung
  2. Day ket qua len Notion duoi Experiments > {beads_project_name}:
     - Tao page tong hop voi diagrams, findings, learnings
  3. Neu la task co nhieu noi dung can hoc tap:
     - Bo sung noi dung theo phuong phap Feynman (giai thich don gian, tim lo hong, tinh chinh)
     - Day noi dung Feynman len Notion
  4. Tao the Trello de theo doi tien do neu can.
  5. Cap nhat self-explores/_index.md voi links moi.
- **Dependencies:** [Task 2]

## Placeholders
Khi dung mau nay, thay the cac gia tri sau:
- `{repo_name}` — Ten repository can nghien cuu (VD: "vllm", "langchain", "claude-agent-sdk")
- `{beads_project_name}` — Ten project trong beads (lay tu bd dolt show hoac .beads/metadata.json)

## Notes
- Giai doan 1 nen dung /youtube skill de tim video — thuong hieu qua hon doc docs thuan.
- Diagram Mermaid nen bat dau tu high-level roi drill down — khong co gang ve het 1 luc.
- Phuong phap Feynman: (1) Chon khai niem, (2) Giai thich nhu dang day nguoi moi, (3) Tim lo hong hieu biet, (4) Don gian hoa va dung analogy.
- Neu repo lon (>100 files), chia Task 1 thanh nhieu sub-tasks theo module.
