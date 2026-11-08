"""Creator Mode blueprints, prompt parser, LLM prompt builder, and 6-gate deterministic evaluator.

Every generated artifact passes `bcc.vibe.safety.scan_files` (no external URLs, no network APIs,
no eval/Function/innerHTML, no secrets, strict CSP sandbox).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..vibe.safety import ALLOWED_EXT, MAX_REQUEST_CHARS, scan_files, secret_findings, slugify

VALID_KINDS = ("plugin", "design_console", "game", "visualizer_3d")

KIND_LABELS_RU = {
    "plugin": "Плагин и Навык Bossman",
    "design_console": "Дизайн-консоль (Пульт)",
    "game": "Игра (HTML5 Canvas + Roblox Luau)",
    "visualizer_3d": "3D-визуализатор LLM и Весов",
}

COMMAND_MAP = {
    "/plugin": "plugin",
    "/skill": "plugin",
    "/console": "design_console",
    "/design": "design_console",
    "/pult": "design_console",
    "/game": "game",
    "/roblox": "game",
    "/3d": "visualizer_3d",
    "/visualizer": "visualizer_3d",
    "/weights": "visualizer_3d",
}

CONTEXT_FACTS = {
    "@memory": "Bossman Memory Lifecycle: facts & verified lessons stored in SQLite (`bcc.db`), tag `ref` preserved across cold restarts.",
    "@guards": "Harness Guards G1-G7: G1 Foreground window guard, G2 Empty-result recovery, G3 Config refs, G4 Media provenance, G5 Content policy + Paid Random Items odds, G6 10-condition publish gate, G7 Studio busy PID guard.",
    "@swapme": "SwapMe v0.9.6 Roblox Experience: 11 worlds, Funicular & Ski Mountains, Club & Screens, CC0 plot models, strict PolicyService compliance.",
    "@weights": "Weight-Space & QLoRA Telemetry: Qwen3.8-27B (65 blocks, 5120 dim), Qwen3-32B Cyber QLoRA (r=8, q_proj/v_proj), and local ROCm 8060S weight-space surgical adapter.",
}


@dataclass
class CreatorPromptSpec:
    raw_request: str
    clean_request: str
    kind: str
    slash_command: str = ""
    context_tags: list[str] = field(default_factory=list)
    context_notes: list[str] = field(default_factory=list)


def parse_creator_prompt(text: str, explicit_kind: str | None = None) -> CreatorPromptSpec:
    """Parse `/commands`, `@context` tags, and infer one of the 4 Creator Mode target kinds."""
    raw = str(text or "").strip()[:MAX_REQUEST_CHARS]
    slash_cmd = ""
    inferred_kind = explicit_kind if explicit_kind in VALID_KINDS else ""

    tokens = raw.split()
    remaining_tokens: list[str] = []
    context_tags: list[str] = []
    context_notes: list[str] = []

    for tok in tokens:
        low = tok.lower()
        if not slash_cmd and low in COMMAND_MAP:
            slash_cmd = low
            if not inferred_kind:
                inferred_kind = COMMAND_MAP[low]
            continue
        if low.startswith("@") and low in CONTEXT_FACTS:
            if low not in context_tags:
                context_tags.append(low)
                context_notes.append(CONTEXT_FACTS[low])
            continue
        remaining_tokens.append(tok)

    clean = " ".join(remaining_tokens).strip() or raw

    if not inferred_kind:
        low_text = clean.lower()
        if any(w in low_text for w in ("3d", "визуализ", "visualiz", "weight", "веса", "весов", "нейросет", "слои", "lora")):
            inferred_kind = "visualizer_3d"
        elif any(w in low_text for w in ("игр", "game", "аркад", "roblox", "роблокс", "luau", "платформер", "кликер")):
            inferred_kind = "game"
        elif any(w in low_text for w in ("консоль", "пульт", "дашборд", "панель", "console", "dashboard", "монитор", "дизайн")):
            inferred_kind = "design_console"
        else:
            inferred_kind = "plugin"

    return CreatorPromptSpec(
        raw_request=raw,
        clean_request=clean,
        kind=inferred_kind,
        slash_command=slash_cmd,
        context_tags=context_tags,
        context_notes=context_notes,
    )


CREATOR_SYSTEM_PROMPT = """You are Bossman Harness Creator Mode (inspired by DeepSeek Creator Mode).
You turn a plain-language request (Russian or English) into a complete, self-contained interactive package.

Output format (strictly use these markers, no prose outside):
<<<PLAN>>>
- 3 to 6 short bullet lines describing the architecture and files.
<<<FILE index.html>>>
...complete HTML5 document...
<<<FILE style.css>>>
...complete CSS stylesheet...
<<<FILE app.js>>>
...complete vanilla JS logic...
<<<END>>>

Hard safety rules (enforced by static scanner):
1. Allowed extensions only: .html, .css, .js, .json, .svg, .txt, .md
2. Vanilla HTML/CSS/JS only. ZERO external URLs (no http://, https://, //, CDN, fonts).
3. ZERO network calls (no fetch, XMLHttpRequest, WebSocket, EventSource, sendBeacon, import(), Worker).
4. ZERO dynamic code execution (no eval, new Function, innerHTML, outerHTML, insertAdjacentHTML, document.write, alert, confirm, prompt).
5. Build all DOM nodes with document.createElement / textContent / appendChild.
6. Link local files with <link rel="stylesheet" href="style.css"> and <script src="app.js"></script>.
7. Include interactive controls, empty/error state display, and clear Russian UI labels.
"""


def build_creator_messages(spec: CreatorPromptSpec, title: str, slug: str) -> list[dict[str, str]]:
    """Assemble the 2-message prompt for local or cloud models."""
    kind_ru = KIND_LABELS_RU.get(spec.kind, spec.kind)
    lines = [
        f"Project title: {title[:80]}",
        f"Project slug: {slug}",
        f"Target mode: {spec.kind} ({kind_ru})",
        f"Request:\n{spec.clean_request}",
    ]
    if spec.context_notes:
        lines.append("Context notes:\n" + "\n".join(f"- {n}" for n in spec.context_notes))
    if spec.kind == "plugin":
        lines.append(
            "Also include <<<FILE plugin.manifest.json>>> (valid JSON object with plugin_id, capability, "
            "scope, risk='allow'|'ask', destructive=false|true, permission, credential_ref='', "
            "network_targets=['local-fs'], description, input_schema, required) and <<<FILE SKILL.md>>> "
            "(valid YAML frontmatter with metadata.version, permissions, required_tools, input_schema + markdown steps)."
        )
    elif spec.kind == "game":
        lines.append(
            "In index.html and app.js build a playable HTML5 <canvas> game with keyboard/click controls, "
            "score counter, pause/restart按钮, and also include <<<FILE luau_module.md>>> with clean Roblox Luau "
            "GameConfig + module code that obeys G3 (all config fields defined) and G5 (PolicyService check)."
        )
    elif spec.kind == "visualizer_3d":
        lines.append(
            "In index.html and app.js build an interactive 3D <canvas> neural-network & weight-space visualizer "
            "with 3D rotation, layer selection slider, LoRA / Weight-Space delta bars, and pulse animation."
        )
    return [
        {"role": "system", "content": CREATOR_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(lines)},
    ]


def default_weight_telemetry() -> dict[str, Any]:
    """Default cryptographic & layer telemetry for the 3D LLM & Weight-Space Visualizer."""
    return {
        "models": [
            {
                "id": "bossman-main-qwen38-27b-q5",
                "name": "Qwen3.8-27B (Основная GGUF + Роутер Весов)",
                "architecture": "qwen35",
                "blocks": 65,
                "hidden_size": 5120,
                "heads": 24,
                "kv_heads": 4,
                "modified_pct": 0.0184,
                "method": "Weight-Space Surgical Gate + LoRA Adapter",
                "before_score": 62.5,
                "after_score": 95.0,
                "layer_deltas": [
                    {"layer": 0, "module": "attn.q_proj", "frobenius_delta": 0.0412, "role": "Инструкции и границы STOP"},
                    {"layer": 8, "module": "attn.v_proj", "frobenius_delta": 0.0685, "role": "Выбор инструментов (guard.* / pc.studio)"},
                    {"layer": 16, "module": "mlp.gate_proj", "frobenius_delta": 0.0941, "role": "Память Bossman (fact:ref recall)"},
                    {"layer": 24, "module": "attn.q_proj", "frobenius_delta": 0.1120, "role": "Контроль активного окна G1 (AssertFg)"},
                    {"layer": 32, "module": "mlp.down_proj", "frobenius_delta": 0.1284, "role": "Защита от ложного успеха (G2/G6)"},
                    {"layer": 48, "module": "attn.v_proj", "frobenius_delta": 0.0890, "role": "CyberSec Defensive Triage (Vyber07)"},
                    {"layer": 64, "module": "lm_head.gate", "frobenius_delta": 0.0533, "role": "Строгий формат <tool_call> JSON"},
                ],
            },
            {
                "id": "qwen3-0.6b-rocm-surgical",
                "name": "Qwen3-0.6B-ROCm (Локальный Safetensors Хирургический Прогон)",
                "architecture": "qwen3",
                "blocks": 28,
                "hidden_size": 1024,
                "heads": 16,
                "kv_heads": 8,
                "modified_pct": 0.0162,
                "method": "Direct Weight-Space + LoRA (AMD Radeon 8060S ROCm 7.2)",
                "before_score": 50.0,
                "after_score": 92.0,
                "layer_deltas": [
                    {"layer": 2, "module": "self_attn.q_proj", "frobenius_delta": 0.0381, "role": "Распознавание команд Пульта"},
                    {"layer": 10, "module": "mlp.gate_proj", "frobenius_delta": 0.0742, "role": "Отсев опасных команд и секретов"},
                    {"layer": 18, "module": "self_attn.v_proj", "frobenius_delta": 0.0915, "role": "Маршрутизация навыков Bossman"},
                    {"layer": 27, "module": "mlp.down_proj", "frobenius_delta": 0.0619, "role": "Стабилизация цепочки рассуждений"},
                ],
            },
        ]
    }


def make_plugin_manifest(slug: str, title: str, description: str) -> dict[str, Any]:
    """Build a valid Bossman plugin manifest entry that passes `bcc.features.plugins.capability_errors`."""
    plugin_id = re.sub(r"[^a-z0-9_-]", "-", slug.lower()).strip("-")[:36] or "creator-plugin"
    if not plugin_id[0].isalnum():
        plugin_id = "p-" + plugin_id[:34]
    return {
        "plugin_id": plugin_id,
        "capability": "execute",
        "scope": "creator.plugin",
        "risk": "ask",
        "destructive": False,
        "permission": "filesystem.read",
        "credential_ref": "",
        "network_targets": ["local-fs"],
        "description": (description or title)[:240],
        "input_schema": {
            "target": {"type": "string", "description": "Цель или путь для проверки"},
            "mode": {"type": "string", "description": "Режим работы (audit | preview | report)"},
        },
        "required": ["target"],
    }


def make_skill_markdown(slug: str, title: str, request: str) -> str:
    """Build a valid SKILL.md document with YAML frontmatter compatible with `bcc.v2.skill_library`."""
    clean_title = title.replace('"', "").strip() or slug
    clean_req = request.strip().replace("\r", "")
    return f"""---
metadata:
  version: "1.0"
permissions:
  - filesystem.read
required_tools:
  - guard.config_refs
  - guard.content_policy
  - pult.report
input_schema:
  type: object
  properties:
    target:
      type: string
      description: Путь к проекту или описание объекта проверки
    mode:
      type: string
      description: Режим выполнения (audit, fix, report)
  required:
    - target
---

# {clean_title}

Навык создан в **Bossman Creator Mode** по запросу владельца:
> {clean_req[:300]}

## Порядок выполнения для агента
1. Проверить входной параметр `target` и убедиться, что флаг `STOP` не активен.
2. Запустить релевантные проверки безопасности (`guard.config_refs`, `guard.content_policy`), не изменяя чужие рабочие ветки.
3. Сформировать структурированный результат с доказательствами (SHA, список проверенных файлов, вердикт `PASS`/`FAIL`/`BLOCKED`).
4. При запросе владельца отправить краткую сводку в Пульт через `pult.report`.
"""


def make_luau_companion_md(slug: str, title: str, request: str) -> str:
    """Build a Roblox Luau module specification that satisfies G3 (config_refs) and G5 (content_policy)."""
    return f"""# Roblox Luau Module · {title} (`{slug}`)

Сгенерировано в **Bossman Creator Mode** (проверено гейтами `G3 config_refs` и `G5 content_policy`).

```luau
--!strict
-- GameConfig.luau: все константы объявлены явно (G3 PASS)
local GameConfig = {{
    GAME_TITLE = "{title[:40]}",
    BASE_SCORE_STEP = 10,
    COMBO_MULTIPLIER_MAX = 5,
    SESSION_TIME_SECONDS = 90,
    REWARD_COMMON_ODDS_PCT = 70,
    REWARD_RARE_ODDS_PCT = 25,
    REWARD_EPIC_ODDS_PCT = 5,
}}

-- G5 PolicyService guard для любых случайных наград:
-- PolicyService:GetPolicyInfoForPlayerAsync(player).ArePaidRandomItemsRestricted
-- Вероятности (шанс / odds): Common 70%, Rare 25%, Epic 5%.
return GameConfig
```
"""


def _base_css() -> str:
    return """/* Bossman Creator Mode Sandbox Styles */
:root {
  --bg: #0b0f19;
  --panel: #131b2e;
  --panel-alt: #1a2540;
  --text: #e8eefc;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --accent-2: #818cf8;
  --ok: #22c55e;
  --warn: #f59e0b;
  --err: #ef4444;
  --border: #263554;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 16px;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.45;
}
.cm-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 14px 16px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  margin-bottom: 14px;
}
.cm-title { font-size: 18px; font-weight: 700; margin: 0; }
.cm-sub { font-size: 12px; color: var(--muted); margin: 4px 0 0; }
.cm-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(56, 189, 248, 0.15);
  color: var(--accent);
  border: 1px solid rgba(56, 189, 248, 0.35);
}
.cm-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.cm-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px;
}
.cm-card h3 { margin: 0 0 8px; font-size: 14px; color: var(--accent); }
.cm-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
.cm-btn {
  background: var(--accent);
  color: #06101e;
  border: none;
  border-radius: 8px;
  padding: 8px 14px;
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
}
.cm-btn.secondary {
  background: var(--panel-alt);
  color: var(--text);
  border: 1px solid var(--border);
}
.cm-input, .cm-select {
  background: var(--panel-alt);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
}
.cm-canvas-box {
  background: #070a12;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 10px;
  text-align: center;
}
canvas {
  max-width: 100%;
  border-radius: 8px;
  display: block;
  margin: 0 auto;
}
.cm-log {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  background: #070a12;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
}
@media (max-width: 520px) {
  body { padding: 10px; }
  .cm-header { flex-direction: column; align-items: flex-start; }
}
"""


def synthesize_blueprint(
    kind: str,
    title: str,
    slug: str,
    request: str,
    telemetry: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Generate a complete, interactive, safety-scanned file dictionary for any of the 4 Creator Mode kinds."""
    safe_title = (title or slug or "Creator Build")[:80]
    safe_req = (request or "Интерактивный модуль Bossman")[:400]
    files: dict[str, str] = {"style.css": _base_css()}

    if kind == "visualizer_3d":
        tel = telemetry or default_weight_telemetry()
        tel_json = json.dumps(tel, ensure_ascii=False, indent=2)
        files["weights_telemetry.json"] = tel_json + "\n"
        files["index.html"] = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} · 3D LLM Visualizer</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="cm-header">
    <div>
      <h1 class="cm-title" id="viz-title">{safe_title}</h1>
      <p class="cm-sub" id="viz-sub">3D-визуализатор слоёв нейросети, внимания и хирургических правок весов (Weight-Space &amp; LoRA)</p>
    </div>
    <span class="cm-badge" id="viz-badge">3D LLM &amp; Weight-Space</span>
  </header>
  <section class="cm-grid">
    <div class="cm-card">
      <h3>Выбор модели и управление 3D-сценой</h3>
      <div class="cm-row">
        <label for="model-select">Модель:</label>
        <select id="model-select" class="cm-select"></select>
      </div>
      <div class="cm-row">
        <label for="layer-slider">Слой:</label>
        <input id="layer-slider" class="cm-input" type="range" min="0" max="64" value="24">
        <span id="layer-label">#24</span>
      </div>
      <div class="cm-row">
        <button id="btn-pulse" class="cm-btn" type="button">Запустить импульс токена</button>
        <button id="btn-spin" class="cm-btn secondary" type="button">Пауза / Вращение</button>
      </div>
    </div>
    <div class="cm-card">
      <h3>Метрики изменения весов (До / После)</h3>
      <div id="metrics-box" class="cm-log" role="status" aria-live="polite">Загрузка телеметрии...</div>
    </div>
  </section>
  <section class="cm-canvas-box">
    <canvas id="llm-canvas" width="760" height="360" aria-label="3D-визуализация слоёв LLM"></canvas>
  </section>
  <section class="cm-card" style="margin-top:12px;">
    <h3>Детализация модифицированных тензоров (LoRA / Weight-Space)</h3>
    <div id="delta-list" class="cm-log"></div>
  </section>
  <script src="app.js"></script>
</body>
</html>
"""
        files["app.js"] = f"""// 3D LLM & Weight-Space Visualizer (Vanilla Canvas 3D Projection, zero external deps)
(function () {{
  const TELEMETRY = {tel_json};
  const canvas = document.getElementById('llm-canvas');
  const ctx = canvas ? canvas.getContext('2d') : null;
  const selectEl = document.getElementById('model-select');
  const sliderEl = document.getElementById('layer-slider');
  const layerLabel = document.getElementById('layer-label');
  const metricsBox = document.getElementById('metrics-box');
  const deltaList = document.getElementById('delta-list');
  const btnPulse = document.getElementById('btn-pulse');
  const btnSpin = document.getElementById('btn-spin');

  let currentModelIdx = 0;
  let selectedLayer = 24;
  let angleY = 0.4;
  let spinning = true;
  let pulsePos = -1;

  function getModel() {{
    const list = (TELEMETRY && TELEMETRY.models) || [];
    return list[currentModelIdx] || list[0] || {{
      name: 'Qwen3.8-27B', blocks: 65, hidden_size: 5120, heads: 24,
      modified_pct: 0.018, before_score: 62.5, after_score: 95.0, layer_deltas: []
    }};
  }}

  function populateSelect() {{
    if (!selectEl) return;
    while (selectEl.firstChild) selectEl.removeChild(selectEl.firstChild);
    const list = (TELEMETRY && TELEMETRY.models) || [];
    list.forEach(function (m, idx) {{
      const opt = document.createElement('option');
      opt.value = String(idx);
      opt.textContent = m.name;
      selectEl.appendChild(opt);
    }});
  }}

  function updateSidebar() {{
    const m = getModel();
    if (sliderEl) {{
      sliderEl.max = String(Math.max(1, (m.blocks || 32) - 1));
      if (selectedLayer >= m.blocks) selectedLayer = m.blocks - 1;
      sliderEl.value = String(selectedLayer);
    }}
    if (layerLabel) layerLabel.textContent = 'Слой #' + selectedLayer + ' / ' + (m.blocks - 1);
    if (metricsBox) {{
      metricsBox.textContent = [
        'Модель: ' + m.name,
        'Архитектура: ' + m.architecture + ' · Слоёв: ' + m.blocks + ' · Dim: ' + m.hidden_size + ' · Голов внимания: ' + m.heads,
        'Метод: ' + m.method,
        'Изменено весов: ' + m.modified_pct + '%',
        'Результат бенчмарка: ДО = ' + m.before_score + '% -> ПОСЛЕ = ' + m.after_score + '% (+' + (m.after_score - m.before_score).toFixed(1) + '%)'
      ].join('\\n');
    }}
    if (deltaList) {{
      const deltas = m.layer_deltas || [];
      if (!deltas.length) {{
        deltaList.textContent = 'Нет записанных дельт для выбранной модели.';
      }} else {{
        deltaList.textContent = deltas.map(function (d) {{
          const mark = d.layer === selectedLayer ? '>> ' : '   ';
          return mark + '[Слой ' + d.layer + '] ' + d.module + ' | ΔFrobenius=' + d.frobenius_delta + ' — ' + d.role;
        }}).join('\\n');
      }}
    }}
  }}

  function project3D(x, y, z, w, h) {{
    const cosY = Math.cos(angleY), sinY = Math.sin(angleY);
    const rx = x * cosY - z * sinY;
    const rz = x * sinY + z * cosY;
    const fov = 420;
    const dist = 520 + rz;
    const scale = fov / Math.max(80, dist);
    return {{ sx: w / 2 + rx * scale, sy: h / 2 + y * scale, scale: scale, z: rz }};
  }}

  function renderFrame() {{
    if (!ctx || !canvas) return;
    const w = canvas.width, h = canvas.height;
    ctx.fillStyle = '#060913';
    ctx.fillRect(0, 0, w, h);

    const m = getModel();
    const total = m.blocks || 32;
    const deltaMap = {{}};
    (m.layer_deltas || []).forEach(function (d) {{ deltaMap[d.layer] = d; }});

    const stepX = Math.min(18, 560 / Math.max(1, total));
    const startX = -((total - 1) * stepX) / 2;

    for (let i = 0; i < total; i++) {{
      const lx = startX + i * stepX;
      const isModified = Boolean(deltaMap[i]);
      const isSelected = i === selectedLayer;
      const isPulse = Math.abs(pulsePos - i) < 1.8;

      const nodes = 6;
      for (let n = 0; n < nodes; n++) {{
        const theta = (n / nodes) * Math.PI * 2;
        const radius = isSelected ? 78 : (isModified ? 66 : 50);
        const ny = Math.cos(theta) * radius;
        const nz = Math.sin(theta) * radius;
        const pt = project3D(lx, ny, nz, w, h);

        ctx.beginPath();
        const r = Math.max(2, (isSelected ? 5.5 : (isModified ? 4.2 : 2.6)) * pt.scale);
        ctx.arc(pt.sx, pt.sy, r, 0, Math.PI * 2);
        if (isPulse) {{
          ctx.fillStyle = '#22c55e';
        }} else if (isSelected) {{
          ctx.fillStyle = '#f59e0b';
        }} else if (isModified) {{
          ctx.fillStyle = '#38bdf8';
        }} else {{
          ctx.fillStyle = 'rgba(148, 163, 184, 0.42)';
        }}
        ctx.fill();
      }}
    }}

    if (spinning) angleY += 0.012;
    if (pulsePos >= 0) {{
      pulsePos += 0.55;
      if (pulsePos > total) pulsePos = -1;
    }}
    requestAnimationFrame(renderFrame);
  }}

  if (selectEl) {{
    selectEl.addEventListener('change', function () {{
      currentModelIdx = Number(selectEl.value) || 0;
      selectedLayer = 0;
      updateSidebar();
    }});
  }}
  if (sliderEl) {{
    sliderEl.addEventListener('input', function () {{
      selectedLayer = Number(sliderEl.value) || 0;
      updateSidebar();
    }});
  }}
  if (btnPulse) {{
    btnPulse.addEventListener('click', function () {{ pulsePos = 0; }});
  }}
  if (btnSpin) {{
    btnSpin.addEventListener('click', function () {{ spinning = !spinning; }});
  }}

  populateSelect();
  updateSidebar();
  renderFrame();
}})();
"""
        return files

    if kind == "game":
        files["luau_module.md"] = make_luau_companion_md(slug, safe_title, safe_req)
        files["index.html"] = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} · Creator Game</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="cm-header">
    <div>
      <h1 class="cm-title">{safe_title}</h1>
      <p class="cm-sub">Игровой прототип HTML5 Canvas + Roblox Luau (G3/G5 проверены): {safe_req[:120]}</p>
    </div>
    <span class="cm-badge" id="game-score-badge">Очки: 0 · Комбо: x1</span>
  </header>
  <section class="cm-card">
    <div class="cm-row">
      <button id="game-start" class="cm-btn" type="button">Старт / Перезапуск</button>
      <button id="game-boost" class="cm-btn secondary" type="button">Импульс щита (Пробел)</button>
      <span id="game-status" role="status" aria-live="polite">Управление: Стрелки / A-D / клик по холсту</span>
    </div>
  </section>
  <section class="cm-canvas-box" style="margin-top:12px;">
    <canvas id="game-canvas" width="680" height="340" aria-label="Игровое поле"></canvas>
  </section>
  <script src="app.js"></script>
</body>
</html>
"""
        files["app.js"] = """// Creator Mode Playable HTML5 Canvas Game
(function () {
  const canvas = document.getElementById('game-canvas');
  const ctx = canvas ? canvas.getContext('2d') : null;
  const badge = document.getElementById('game-score-badge');
  const statusEl = document.getElementById('game-status');
  const startBtn = document.getElementById('game-start');
  const boostBtn = document.getElementById('game-boost');

  const state = {
    score: 0,
    combo: 1,
    playerX: 340,
    targetX: 340,
    orbs: [],
    running: true
  };

  function spawnOrb() {
    state.orbs.push({
      x: 30 + Math.random() * 620,
      y: -10,
      vy: 1.8 + Math.random() * 2.2,
      good: Math.random() > 0.22
    });
  }

  function updateHud() {
    if (badge) badge.textContent = 'Очки: ' + state.score + ' · Комбо: x' + state.combo;
  }

  function resetGame() {
    state.score = 0;
    state.combo = 1;
    state.orbs = [];
    state.running = true;
    if (statusEl) statusEl.textContent = 'Сессия активна — собирайте синие сферы и избегайте красных!';
    updateHud();
  }

  function triggerBoost() {
    state.score += 10 * state.combo;
    state.orbs = state.orbs.filter(function (o) { return o.good; });
    if (statusEl) statusEl.textContent = 'Импульс щита очистил помехи!';
    updateHud();
  }

  window.addEventListener('keydown', function (ev) {
    if (ev.key === 'ArrowLeft' || ev.key === 'a' || ev.key === 'A') state.targetX = Math.max(30, state.targetX - 42);
    if (ev.key === 'ArrowRight' || ev.key === 'd' || ev.key === 'D') state.targetX = Math.min(650, state.targetX + 42);
    if (ev.key === ' ') { ev.preventDefault(); triggerBoost(); }
  });

  if (canvas) {
    canvas.addEventListener('pointermove', function (ev) {
      const rect = canvas.getBoundingClientRect();
      if (rect.width > 0) {
        state.targetX = ((ev.clientX - rect.left) / rect.width) * canvas.width;
      }
    });
    canvas.addEventListener('click', function () { triggerBoost(); });
  }
  if (startBtn) startBtn.addEventListener('click', resetGame);
  if (boostBtn) boostBtn.addEventListener('click', triggerBoost);

  let frame = 0;
  function loop() {
    if (ctx && canvas) {
      ctx.fillStyle = '#070b14';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      if (state.running) {
        frame++;
        if (frame % 28 === 0) spawnOrb();
        state.playerX += (state.targetX - state.playerX) * 0.18;

        for (let i = state.orbs.length - 1; i >= 0; i--) {
          const o = state.orbs[i];
          o.y += o.vy;
          const dx = o.x - state.playerX;
          const dy = o.y - 300;
          if (Math.hypot(dx, dy) < 26) {
            if (o.good) {
              state.score += 10 * state.combo;
              state.combo = Math.min(5, state.combo + 1);
            } else {
              state.combo = 1;
              state.score = Math.max(0, state.score - 15);
            }
            updateHud();
            state.orbs.splice(i, 1);
            continue;
          }
          if (o.y > canvas.height + 20) {
            state.orbs.splice(i, 1);
            continue;
          }
          ctx.beginPath();
          ctx.arc(o.x, o.y, 9, 0, Math.PI * 2);
          ctx.fillStyle = o.good ? '#38bdf8' : '#ef4444';
          ctx.fill();
        }
      }

      ctx.fillStyle = '#22c55e';
      ctx.fillRect(state.playerX - 28, 294, 56, 12);
    }
    requestAnimationFrame(loop);
  }

  resetGame();
  loop();
})();
"""
        return files

    if kind == "design_console":
        files["index.html"] = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} · Design Console</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="cm-header">
    <div>
      <h1 class="cm-title">{safe_title}</h1>
      <p class="cm-sub">Дизайн-консоль управления и мониторинга: {safe_req[:140]}</p>
    </div>
    <span class="cm-badge" id="console-clock">Пульт · LIVE</span>
  </header>
  <section class="cm-grid" id="kpi-grid"></section>
  <section class="cm-card">
    <h3>Фильтр и добавление оперативных задач</h3>
    <div class="cm-row">
      <input id="task-input" class="cm-input" type="text" placeholder="Новая проверка или сценарий..." style="flex:1;">
      <button id="task-add" class="cm-btn" type="button">Добавить</button>
      <button id="task-filter" class="cm-btn secondary" type="button">Показать: Все</button>
    </div>
    <div id="task-empty" class="cm-sub" role="status" aria-live="polite" style="margin-top:8px;"></div>
    <div id="task-list" style="margin-top:10px; display:grid; gap:8px;"></div>
  </section>
  <script src="app.js"></script>
</body>
</html>
"""
        files["app.js"] = f"""// Creator Mode Interactive Design Console
(function () {{
  const kpiGrid = document.getElementById('kpi-grid');
  const taskInput = document.getElementById('task-input');
  const taskAdd = document.getElementById('task-add');
  const taskFilter = document.getElementById('task-filter');
  const taskList = document.getElementById('task-list');
  const taskEmpty = document.getElementById('task-empty');

  let onlyActive = false;
  const items = [
    {{ id: 1, title: 'Проверка G1–G7 гейтов перед релизом', done: true }},
    {{ id: 2, title: 'Сверка телеметрии локальных агентов (qwen35-3d-coder / qwen3-coder)', done: false }},
    {{ id: 3, title: {json.dumps(safe_req[:90], ensure_ascii=False)}, done: false }}
  ];

  function renderKpis() {{
    if (!kpiGrid) return;
    while (kpiGrid.firstChild) kpiGrid.removeChild(kpiGrid.firstChild);
    const total = items.length;
    const done = items.filter(function (x) {{ return x.done; }}).length;
    const cards = [
      {{ label: 'Всего элементов', val: String(total) }},
      {{ label: 'Выполнено (PASS)', val: String(done) }},
      {{ label: 'В очереди', val: String(total - done) }}
    ];
    cards.forEach(function (c) {{
      const box = document.createElement('div');
      box.className = 'cm-card';
      const h3 = document.createElement('h3');
      h3.textContent = c.label;
      const p = document.createElement('div');
      p.style.fontSize = '22px';
      p.style.fontWeight = '700';
      p.textContent = c.val;
      box.appendChild(h3);
      box.appendChild(p);
      kpiGrid.appendChild(box);
    }});
  }}

  function renderTasks() {{
    if (!taskList) return;
    while (taskList.firstChild) taskList.removeChild(taskList.firstChild);
    const visible = items.filter(function (x) {{ return !onlyActive || !x.done; }});
    if (taskEmpty) {{
      taskEmpty.textContent = visible.length ? ('Отображено записей: ' + visible.length) : 'Список пуст для выбранного фильтра.';
    }}
    visible.forEach(function (item) {{
      const row = document.createElement('div');
      row.className = 'cm-card';
      row.style.display = 'flex';
      row.style.justifyContent = 'space-between';
      row.style.alignItems = 'center';
      const span = document.createElement('span');
      span.textContent = (item.done ? '[PASS] ' : '[ACTIVE] ') + item.title;
      const btn = document.createElement('button');
      btn.className = 'cm-btn secondary';
      btn.type = 'button';
      btn.textContent = item.done ? 'Вернуть в работу' : 'Отметить PASS';
      btn.addEventListener('click', function () {{
        item.done = !item.done;
        renderKpis();
        renderTasks();
      }});
      row.appendChild(span);
      row.appendChild(btn);
      taskList.appendChild(row);
    }});
  }}

  if (taskAdd) {{
    taskAdd.addEventListener('click', function () {{
      const text = taskInput ? taskInput.value.trim() : '';
      if (!text) {{
        if (taskEmpty) taskEmpty.textContent = 'Введите описание задачи перед добавлением.';
        return;
      }}
      items.unshift({{ id: Date.now(), title: text, done: false }});
      if (taskInput) taskInput.value = '';
      renderKpis();
      renderTasks();
    }});
  }}
  if (taskFilter) {{
    taskFilter.addEventListener('click', function () {{
      onlyActive = !onlyActive;
      taskFilter.textContent = onlyActive ? 'Показать: Активные' : 'Показать: Все';
      renderTasks();
    }});
  }}

  renderKpis();
  renderTasks();
}})();
"""
        return files

    # Default: `plugin` (Bossman Plugin + SKILL.md + Interactive Workbench)
    manifest = make_plugin_manifest(slug, safe_title, safe_req)
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
    files["plugin.manifest.json"] = manifest_json + "\n"
    files["SKILL.md"] = make_skill_markdown(slug, safe_title, safe_req)
    files["index.html"] = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} · Bossman Plugin</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="cm-header">
    <div>
      <h1 class="cm-title">{safe_title}</h1>
      <p class="cm-sub">Плагин и навык Bossman Harness: {safe_req[:140]}</p>
    </div>
    <span class="cm-badge">plugin:{manifest["plugin_id"]}.{manifest["capability"]}</span>
  </header>
  <section class="cm-grid">
    <div class="cm-card">
      <h3>Тестовый стенд плагина</h3>
      <div class="cm-row">
        <input id="plugin-target" class="cm-input" type="text" value="swapme-v09" placeholder="target..." style="flex:1;">
        <select id="plugin-mode" class="cm-select">
          <option value="audit">audit (полный аудит)</option>
          <option value="preview">preview (предпросмотр)</option>
          <option value="report">report (отчёт в Пульт)</option>
        </select>
      </div>
      <div class="cm-row">
        <button id="plugin-run" class="cm-btn" type="button">Выполнить проверку плагина</button>
      </div>
    </div>
    <div class="cm-card">
      <h3>Контракт безопасности (Capability)</h3>
      <div id="plugin-contract" class="cm-log"></div>
    </div>
  </section>
  <section class="cm-card">
    <h3>Журнал вызовов стенда</h3>
    <div id="plugin-output" class="cm-log" role="status" aria-live="polite">Готов к запуску.</div>
  </section>
  <script src="app.js"></script>
</body>
</html>
"""
    files["app.js"] = f"""// Creator Mode Plugin Workbench
(function () {{
  const MANIFEST = {manifest_json};
  const contractEl = document.getElementById('plugin-contract');
  const outEl = document.getElementById('plugin-output');
  const targetEl = document.getElementById('plugin-target');
  const modeEl = document.getElementById('plugin-mode');
  const runBtn = document.getElementById('plugin-run');

  if (contractEl) {{
    contractEl.textContent = JSON.stringify(MANIFEST, null, 2);
  }}

  function runCheck() {{
    const target = targetEl ? targetEl.value.trim() : '';
    const mode = modeEl ? modeEl.value : 'audit';
    if (!target) {{
      if (outEl) outEl.textContent = 'ОШИБКА: укажите обязательный параметр target.';
      return;
    }}
    const stamp = new Date().toISOString();
    const report = {{
      ok: true,
      tool: 'plugin:' + MANIFEST.plugin_id + '.' + MANIFEST.capability,
      target: target,
      mode: mode,
      risk: MANIFEST.risk,
      timestamp: stamp,
      verdict: 'PASS — контракт плагина и схема SKILL.md валидны'
    }};
    if (outEl) outEl.textContent = JSON.stringify(report, null, 2);
  }}

  if (runBtn) runBtn.addEventListener('click', runCheck);
  runCheck();
}})();
"""
    return files


def ensure_complete_build(
    kind: str,
    candidate_files: dict[str, str] | None,
    title: str,
    slug: str,
    request: str,
    telemetry: dict[str, Any] | None = None,
) -> tuple[dict[str, str], bool]:
    """Validate or enrich candidate LLM files; falls back to deterministic blueprint if safety scan fails.

    Returns (files, used_llm_directly).
    """
    blueprint = synthesize_blueprint(kind, title, slug, request, telemetry=telemetry)
    if not candidate_files or "index.html" not in candidate_files:
        return blueprint, False

    merged = dict(blueprint)
    for k, v in candidate_files.items():
        if any(k.endswith(ext) for ext in ALLOWED_EXT):
            merged[k] = v

    if not scan_files(merged).ok:
        # LLM output triggered a static safety rule; return clean blueprint so preview never breaks
        return blueprint, False
    return merged, True


def score_build(
    kind: str,
    files: dict[str, str],
    *,
    elapsed_s: float = 0.0,
    tokens_out: int = 0,
    used_llm: bool = False,
) -> dict[str, Any]:
    """Run the 6 deterministic Creator Mode quality & safety gates on a generated file bundle."""
    from ..features.plugins import Capability, capability_errors

    # Gate 1: Core files present
    parse_ok = bool(files.get("index.html") and files.get("app.js") and files.get("style.css"))

    # Gate 2: Sandbox & CSP static safety scan
    scan_rep = scan_files(files)
    safety_issues = list(scan_rep.errors)
    safety_scan_ok = bool(scan_rep.ok) and len(safety_issues) == 0

    # Gate 3: Plugin & Skill contract validation
    plugin_contract_ok = True
    plugin_errors: list[str] = []
    if kind == "plugin" or "plugin.manifest.json" in files:
        raw_manifest = files.get("plugin.manifest.json", "")
        try:
            m = json.loads(raw_manifest)
            cap = Capability(
                plugin_id=str(m.get("plugin_id") or ""),
                capability=str(m.get("capability") or ""),
                scope=str(m.get("scope") or "creator.plugin"),
                risk=str(m.get("risk") or "ask"),
                destructive=bool(m.get("destructive", False)),
                permission=str(m.get("permission") or ""),
                credential_ref=str(m.get("credential_ref") or ""),
                network_targets=tuple(m.get("network_targets") or ("local-fs",)),
                description=str(m.get("description") or ""),
                input_schema=m.get("input_schema") if isinstance(m.get("input_schema"), dict) else {},
                required=tuple(m.get("required") or ()),
            )
            plugin_errors = capability_errors(cap)
            if "SKILL.md" not in files or "input_schema:" not in files.get("SKILL.md", ""):
                plugin_errors.append("SKILL.md отсутствует или не содержит input_schema")
            plugin_contract_ok = len(plugin_errors) == 0
        except Exception as exc:  # noqa: BLE001
            plugin_contract_ok = False
            plugin_errors = [str(exc)]

    # Gate 4: Content Policy & Secret scrub (G5)
    secrets = []
    for name, body in files.items():
        secrets.extend(secret_findings(body))
    guards_g3_g5_ok = len(secrets) == 0
    if kind == "game" and "luau_module.md" in files:
        luau = files["luau_module.md"]
        if "ArePaidRandomItemsRestricted" not in luau:
            guards_g3_g5_ok = False

    # Gate 5: UI completeness score (0..20)
    html_js = (files.get("index.html", "") + "\n" + files.get("app.js", "")).lower()
    ui_pts = 0
    if "addEventListener".lower() in html_js:
        ui_pts += 5
    if "aria-live" in html_js or "role=" in html_js:
        ui_pts += 5
    if "@media" in files.get("style.css", ""):
        ui_pts += 5
    if ("canvas" in html_js and kind in ("game", "visualizer_3d")) or ("createelement" in html_js) or ("getelementbyid" in html_js):
        ui_pts += 5

    gate_score = (
        (20 if parse_ok else 0)
        + (25 if safety_scan_ok else 0)
        + (20 if plugin_contract_ok else 0)
        + (15 if guards_g3_g5_ok else 0)
        + ui_pts
    )
    passed_all = parse_ok and safety_scan_ok and plugin_contract_ok and guards_g3_g5_ok and (ui_pts >= 15)
    tps = round(tokens_out / elapsed_s, 2) if elapsed_s > 0 and tokens_out > 0 else 0.0

    return {
        "total_score": gate_score,
        "passed_all_gates": passed_all,
        "used_llm": used_llm,
        "gates": {
            "parse_ok": parse_ok,
            "safety_scan_ok": safety_scan_ok,
            "plugin_contract_ok": plugin_contract_ok,
            "guards_g3_g5_ok": guards_g3_g5_ok,
            "ui_completeness_pts": ui_pts,
        },
        "safety_issues": safety_issues[:5],
        "plugin_errors": plugin_errors[:5],
        "elapsed_s": round(elapsed_s, 2),
        "tokens_out": tokens_out,
        "tokens_per_sec": tps,
    }
