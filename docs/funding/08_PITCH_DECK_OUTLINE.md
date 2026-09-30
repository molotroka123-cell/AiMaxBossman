# 08 — Pitch deck outline / План презентации

English deck (programs are English-language); speaker notes in Russian. 12 slides. Every number on a slide must come
from 07 (measured) or be labelled PLAN / HYPOTHESIS. No logos of NVIDIA/Microsoft/AWS/Google on slides unless the
program's brand rules allow it (owner checks).

| # | Slide title | Content | Source | Заметки (RU) |
|---|---|---|---|---|
| 1 | Bossman + Jev | one line: "A local autonomous engineer that ships only what two independent AI reviewers and a human approve" | 01 | Название компании/сайт вписывает владелец |
| 2 | Problem | unsafe or cloud-only coding agents; privacy; uncontrolled spend | 01 | Без выдуманных рыночных цифр |
| 3 | How it works | diagram Jev → Bossman control plane → Claude/Codex writer+reviewer → tests → dual SHA approval → staging → user Apply | 02 §1 | Подчеркнуть: агент не мержит сам |
| 4 | Safety by constitution | user-owned, SHA-pinned rules; release tiers; autonomy L0–L4; money/messages/keys always human | 02 §2 | |
| 5 | Working today | 6 measured bullets (CI 11/11, Windows bundle PASS, real computer-use a–g, GPT-OSS 7/7 90 s, 24/7 loop 211 cycles $0, p95 6688→168 ms) | 07 A1–A12 | Каждая цифра с SHA в сносках |
| 6 | What is not done yet | autonomy loop in progress, M1 not reached, learning NOT_PROVEN, no customers | 07 §B | Честность = доверие рецензента |
| 7 | Why 700+ GB | DeepSeek-V3.2 685B / ≈689.5 GB vs 141 GB H200, 96 GB ZeroGPU, 128 GB owner machine; tp 8 reference; DGX Station 748 GB and its caveat | 03 §5 | Сказать про LPDDR5X-оговорку |
| 8 | Benchmark plan | 6 arms, 60 fixed tasks, metrics, pre-registered decision rule | 03 §2–4 | PLAN |
| 9 | Ask and budget | ~42 node-hours 8×H200; $1.2–1.5K at list price (derived); burn order; kill switches | 04 | PLAN |
| 10 | Path to revenue | P1–P3 workflows, price hypotheses, kill criteria | 05 §1–2 | HYPOTHESIS, выручки нет |
| 11 | Milestones | M1–M6 with current status (M1 not reached) | owner plan | |
| 12 | Team and contact | owner name, role, company, website, email | 09 | Заполняет владелец |

Appendix slides: evidence index table (07 §A), architecture contract types (`docs/autonomy/AUTONOMY_CONTRACT.md`),
source list with access dates.
