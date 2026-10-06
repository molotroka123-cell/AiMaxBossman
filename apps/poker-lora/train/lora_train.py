"""LoRA SFT on the exported chat data (pokerlora.sft). Plain PyTorch loop (no trl). NOT_RUN in the build container: no torch, no GPU, no weights.
Run it only on a machine where train/env_check.py says CAN_TRY_SMOKE_TRAIN and a smoke train (--max-steps 20) completes.
RunPod or any paid cloud is NOT used without the owner's explicit permission.

  python train/lora_train.py --base PATH_OR_ID --data DIR_WITH_sft_train.jsonl --out OUT --r 16 --alpha 32 --epochs 2 [--dry-run]"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


def sha_of(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--data", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--r", type=int, default=16); ap.add_argument("--alpha", type=int, default=32); ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--lr", type=float, default=2e-4); ap.add_argument("--epochs", type=float, default=2.0); ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--batch", type=int, default=4); ap.add_argument("--max-len", type=int, default=1536); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--targets", default="q_proj,k_proj,v_proj,o_proj"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    data = Path(a.data) / "sft_train.jsonl"
    rows = [json.loads(l) for l in data.read_text(encoding="utf-8").splitlines()]
    manifest = {"base": a.base, "r": a.r, "alpha": a.alpha, "lr": a.lr, "epochs": a.epochs, "max_steps": a.max_steps, "batch": a.batch, "seed": a.seed,
                "data": str(data), "data_sha256": sha_of(data), "examples": len(rows), "targets": a.targets,
                "git_sha": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or "unknown"}
    if a.dry_run:
        print(json.dumps({"dry_run": True, **manifest, "first_example_chars": sum(len(m["content"]) for m in rows[0]["messages"])}, indent=1)); return 0
    try:
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:  # noqa: BLE001
        print(f"cannot train here: {type(exc).__name__}: {exc}. Run train/env_check.py", file=sys.stderr); return 2
    torch.manual_seed(a.seed)
    tok = AutoTokenizer.from_pretrained(a.base)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModelForCausalLM.from_pretrained(a.base, torch_dtype=torch.bfloat16 if dev == "cuda" else torch.float32).to(dev)
    model = get_peft_model(model, LoraConfig(r=a.r, lora_alpha=a.alpha, lora_dropout=a.dropout, target_modules=a.targets.split(","), task_type="CAUSAL_LM"))
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=a.lr)

    def encode(r):
        prompt = tok.apply_chat_template(r["messages"][:2], tokenize=False, add_generation_prompt=True)
        full = prompt + r["messages"][2]["content"] + (tok.eos_token or "")
        ids = tok(full, truncation=True, max_length=a.max_len, return_tensors="pt")["input_ids"][0]
        n_prompt = len(tok(prompt, truncation=True, max_length=a.max_len)["input_ids"])
        labels = ids.clone(); labels[:n_prompt] = -100                 # loss only on the answer
        return ids, labels
    steps_total = a.max_steps if a.max_steps > 0 else int(len(rows) * a.epochs / a.batch)
    model.train(); t0 = time.time(); step = 0
    import random
    rng = random.Random(a.seed)
    while step < steps_total:
        batch = rng.sample(rows, min(a.batch, len(rows)))
        loss_acc = 0.0
        for r in batch:
            ids, labels = encode(r)
            out = model(input_ids=ids[None].to(dev), labels=labels[None].to(dev))
            (out.loss / len(batch)).backward(); loss_acc += float(out.loss) / len(batch)
        opt.step(); opt.zero_grad(); step += 1
        if step % 10 == 0:
            print(f"step {step}/{steps_total} loss {loss_acc:.4f}", flush=True)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    model.save_pretrained(a.out)
    manifest.update({"steps": step, "seconds": round(time.time() - t0), "device": dev})
    (Path(a.out) / "training_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
