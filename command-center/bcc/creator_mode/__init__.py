"""Bossman Creator Mode (05.11.2026): natural-language creator for Harness Agent & Web Pult.

Inspired by DeepSeek Harness Creator Mode and adapted to Bossman's strict safety & guard architecture:
- Describe a feature in plain Russian or English (supports `/plugin`, `/console`, `/game`, `/3d` commands
  and `@memory`, `@guards`, `@swapme`, `@weights` context tags).
- Builds 4 artifact classes:
  1. `plugin`         — Bossman Plugin (`plugin.manifest.json` + `SKILL.md` + interactive test console)
  2. `design_console` — Interactive Design Console / Web Pult widget (`index.html`, `style.css`, `app.js`)
  3. `game`           — Playable HTML5 Canvas mini-game + Luau (`game_module.luau`, `GameConfig.luau`) checked by G3/G5
  4. `visualizer_3d`  — Interactive 3D LLM & Weight-Space Visualizer with real layer/LoRA telemetry
- Works with any local Ollama model (`qwen35-3d-coder:latest`, `qwen3-coder:30b`, `bossman-fast-qwen36-35b-a3b-q5:latest`,
  `ace-qwen3-17b:latest`, `bossman-main-qwen38-27b-q5:latest`), free cloud models, or deterministic instant synthesis.
- Includes a Multi-Agent Overnight Arena (`run_arena`) that benchmarks multiple local agents on the same prompt and
  scores every build on 6 deterministic gates (parse, safety scan, plugin contract, G3/G5 guards, UI completeness, speed).
"""
from .service import CreatorError, CreatorModeService  # noqa: F401
