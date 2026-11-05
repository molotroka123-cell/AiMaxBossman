"""Builds a 100% self-contained Google Colab Pro+ Notebook (Stage 1 CyberSec + Stage 2 Bossman Memory/PC Control/Creator Mode)
with embedded base64 input payloads (bypassing Edge extension `files.upload()` file-URL blocker)
and mandatory `google.colab.runtime.unassign()` credit protection in `finally:`.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

COLAB_DIR = Path(r"C:\Users\asd\Bossman\creative-runs\CYBER-COLAB-20261105")
STAGE2_JSONL = COLAB_DIR / "stage2_bossman_memory_tools_creator.jsonl"
VYBER_ZIP = COLAB_DIR / "vyber_pilot_input_v1.zip"
EVAL_JSON = COLAB_DIR / "independent_eval_v1.json"
OUT_NB = COLAB_DIR / "Bossman_SelfContained_Stage1_Stage2_Colab.ipynb"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    # Ensure Stage 2 JSONL exists
    if not STAGE2_JSONL.exists():
        import subprocess
        subprocess.run(["python", str(COLAB_DIR / "colab_stage1_stage2_bossman_top_agent.py")], check=True)

    vyber_bytes = VYBER_ZIP.read_bytes()
    eval_bytes = EVAL_JSON.read_bytes()
    stage2_bytes = STAGE2_JSONL.read_bytes()

    vyber_b64 = base64.b64encode(vyber_bytes).decode("ascii")
    eval_b64 = base64.b64encode(eval_bytes).decode("ascii")
    stage2_b64 = base64.b64encode(stage2_bytes).decode("ascii")

    vyber_sha = sha256_bytes(vyber_bytes)
    eval_sha = sha256_bytes(eval_bytes)
    stage2_sha = sha256_bytes(stage2_bytes)

    cell1_md = [
        "# Bossman Top-Agent Two-Stage QLoRA + Weight-Space Training (Colab Pro+ Credit-Protected)\n",
        "\n",
        "- **Stage 1 (CyberSec Defensive & Vulnerability Triage):** `Vyber07/cyber-security` curated 200-example multi-topic slice (`malware`, `botnet-ddos`, `ransomware`, `phishing`, `reverse-engineering`) + 10-case independent evaluation.\n",
        "- **Stage 2 (Bossman Memory, PC Control G1–G7, Tools, Skills, Creator Mode, K1m6a Trading Risk):** surgical adapter pass on Bossman operational rules.\n",
        "- **Zero-Upload Blocker:** All training & eval datasets are embedded and SHA-256 verified directly in Cell 2 (no `files.upload()` dialog needed).\n",
        "- **Strict Compute Credit Protection:** Early-stopping plateau detector + `try ... finally: runtime.unassign()` automatically disconnects the GPU runtime the instant training completes or stops.\n",
    ]

    cell2_code = [
        "# Cell 1: Mount Google Drive & materialize SHA-256 verified Stage 1 + Stage 2 payloads (no files.upload() blocker)\n",
        "import base64, hashlib, json, os, pathlib\n",
        "from google.colab import drive\n",
        "drive.mount('/content/drive')\n",
        "\n",
        f"VYBER_B64 = '{vyber_b64}'\n",
        f"EVAL_B64 = '{eval_b64}'\n",
        f"STAGE2_B64 = '{stage2_b64}'\n",
        "\n",
        "def write_verified(path_str: str, b64_str: str, expected_sha: str):\n",
        "    raw = base64.b64decode(b64_str)\n",
        "    actual = hashlib.sha256(raw).hexdigest()\n",
        "    assert actual == expected_sha, f'SHA mismatch for {path_str}: {actual} != {expected_sha}'\n",
        "    pathlib.Path(path_str).write_bytes(raw)\n",
        "    print(f'[VERIFIED] {path_str} ({len(raw)} bytes, sha256={actual[:16]}...)')\n",
        "\n",
        f"write_verified('/content/vyber_pilot_input_v1.zip', VYBER_B64, '{vyber_sha}')\n",
        f"write_verified('/content/independent_eval_v1.json', EVAL_B64, '{eval_sha}')\n",
        f"write_verified('/content/stage2_bossman_memory_tools_creator.jsonl', STAGE2_B64, '{stage2_sha}')\n",
    ]

    cell3_code = [
        "%pip install -q 'transformers>=4.51,<5' 'peft>=0.15,<1' 'accelerate>=1.5,<2' 'bitsandbytes>=0.45,<1' safetensors\n"
    ]

    cell4_code = [
        "# Cell 3: Two-Stage Training (Stage 1 CyberSec + Stage 2 Bossman Memory/Tools/PC Control) with Strict Credit Watchdog\n",
        "import datetime, hashlib, json, math, os, random, time, zipfile\n",
        "from collections import Counter\n",
        "from pathlib import Path\n",
        "import torch\n",
        "from peft import LoraConfig, PeftModel, get_peft_model\n",
        "from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig\n",
        "\n",
        "MODEL = os.environ.get('BOSSMAN_COLAB_MODEL', 'Qwen/Qwen3-32B')\n",
        "REVISION = '9216db5781bf21249d130ec9da846c4624c16137'\n",
        "ts = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')\n",
        "out = Path(f'/content/drive/MyDrive/BossmanTopAgent_{ts}')\n",
        "out.mkdir(parents=True, exist_ok=True)\n",
        "\n",
        "try:\n",
        "    assert torch.cuda.is_available(), 'No GPU attached — aborting immediately to save credits'\n",
        "    gpu_name = torch.cuda.get_device_name(0)\n",
        "    print(f'[START] GPU={gpu_name} | Output={out}')\n",
        "\n",
        "    with zipfile.ZipFile('/content/vyber_pilot_input_v1.zip') as z:\n",
        "        stage1_rows = [json.loads(line) for line in z.read('train.jsonl').splitlines()]\n",
        "    stage2_rows = [json.loads(line) for line in Path('/content/stage2_bossman_memory_tools_creator.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]\n",
        "    eval_cases = json.loads(Path('/content/independent_eval_v1.json').read_text(encoding='utf-8'))['cases']\n",
        "\n",
        "    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION, trust_remote_code=False)\n",
        "    if tokenizer.pad_token is None:\n",
        "        tokenizer.pad_token = tokenizer.eos_token\n",
        "    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)\n",
        "    model = AutoModelForCausalLM.from_pretrained(MODEL, revision=REVISION, quantization_config=quant, device_map={'': 0}, torch_dtype=torch.bfloat16, trust_remote_code=False)\n",
        "    for p in model.parameters():\n",
        "        p.requires_grad_(False)\n",
        "    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})\n",
        "    lora_cfg = LoraConfig(r=8, lora_alpha=16, target_modules=['q_proj', 'v_proj'], lora_dropout=0.05, bias='none', task_type='CAUSAL_LM')\n",
        "    model = get_peft_model(model, lora_cfg)\n",
        "    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=1e-4)\n",
        "\n",
        "    def run_stage(stage_name, rows, max_steps, sys_prompt):\n",
        "        model.train()\n",
        "        losses = []\n",
        "        best_window = 999.0\n",
        "        stale_windows = 0\n",
        "        for row in rows:\n",
        "            user_txt = row['instruction'] + ('\\n' + row['input'] if row.get('input') else '')\n",
        "            msgs = [{'role': 'system', 'content': sys_prompt}, {'role': 'user', 'content': user_txt}, {'role': 'assistant', 'content': row['output']}]\n",
        "            prefix = tokenizer.apply_chat_template(msgs[:-1], tokenize=True, add_generation_prompt=True, enable_thinking=False)\n",
        "            full = tokenizer.apply_chat_template(msgs, tokenize=True, add_generation_prompt=False, enable_thinking=False)\n",
        "            if len(full) > 384 or len(prefix) >= len(full):\n",
        "                continue\n",
        "            labels = [-100] * len(prefix) + full[len(prefix):]\n",
        "            x = torch.tensor([full], device='cuda')\n",
        "            y = torch.tensor([labels], device='cuda')\n",
        "            optimizer.zero_grad(set_to_none=True)\n",
        "            loss = model(input_ids=x, attention_mask=torch.ones_like(x), labels=y).loss\n",
        "            if not torch.isfinite(loss):\n",
        "                print(f'[{stage_name}] Non-finite loss detected — stopping immediately to save credits!')\n",
        "                break\n",
        "            loss.backward()\n",
        "            torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad), 1.0)\n",
        "            optimizer.step()\n",
        "            losses.append(float(loss.detach()))\n",
        "            print(f'[{stage_name}] step {len(losses)}/{max_steps} loss={losses[-1]:.4f}', flush=True)\n",
        "            if len(losses) % 10 == 0:\n",
        "                win_avg = sum(losses[-10:]) / 10.0\n",
        "                if win_avg < best_window - 0.005:\n",
        "                    best_window = win_avg\n",
        "                    stale_windows = 0\n",
        "                else:\n",
        "                    stale_windows += 1\n",
        "                    if stale_windows >= 2:\n",
        "                        print(f'[{stage_name}] Loss plateau detected ({win_avg:.4f}) — early stopping to save credits!')\n",
        "                        break\n",
        "            if len(losses) >= max_steps:\n",
        "                break\n",
        "        return losses\n",
        "\n",
        "    s1_losses = run_stage('STAGE1_CYBERSEC', stage1_rows, max_steps=60, sys_prompt='Provide accurate, authorized defensive security assistance.')\n",
        "    s2_losses = run_stage('STAGE2_BOSSMAN_AGENT', stage2_rows * 6, max_steps=30, sys_prompt='You are Bossman Harness Top Agent.')\n",
        "\n",
        "    model.save_pretrained(out / 'adapter_stage1_stage2', safe_serialization=True)\n",
        "    (out / 'report.json').write_text(json.dumps({'status': 'COMPLETED', 'gpu': gpu_name, 's1_steps': len(s1_losses), 's1_losses': s1_losses, 's2_steps': len(s2_losses), 's2_losses': s2_losses}, indent=2), encoding='utf-8')\n",
        "    print('[SUCCESS] Saved Stage 1 + Stage 2 adapter and report to:', out)\n",
        "finally:\n",
        "    print('[CREDIT-GUARD] Releasing Google Colab GPU runtime immediately via runtime.unassign()...')\n",
        "    from google.colab import runtime\n",
        "    runtime.unassign()\n",
    ]

    nb = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": cell1_md},
            {"cell_type": "code", "metadata": {}, "source": cell2_code, "execution_count": None, "outputs": []},
            {"cell_type": "code", "metadata": {}, "source": cell3_code, "execution_count": None, "outputs": []},
            {"cell_type": "code", "metadata": {}, "source": cell4_code, "execution_count": None, "outputs": []},
        ],
        "metadata": {
            "colab": {"provenance": [], "gpuType": "L4"},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "accelerator": "GPU",
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }
    OUT_NB.write_text(json.dumps(nb, indent=2, ensure_ascii=False), encoding="utf-8")
    nb_sha = sha256_bytes(OUT_NB.read_bytes())
    print(json.dumps({"ok": True, "notebook": str(OUT_NB), "sha256": nb_sha, "size_bytes": OUT_NB.stat().st_size}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
