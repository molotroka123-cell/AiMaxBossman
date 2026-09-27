"""LoRA fine-tune of a local model on brief -> spec pairs (Unsloth). STATUS: NOT_TESTED.

Why and when: few-shot + validator repair (generate_spec.py) is the first line and needs no
training. Fine-tune only after measuring that base (few-shot) against a held-out brief set, and
promote the adapter only if the zero-shot valid-rate and owner rating beat that base on
the SAME held-out briefs (docs/v1.8/MOTION_STUDIO.md, "Learning loop").

Data: dataset/brief_to_spec.jsonl (owner-approved rows only; two seed rows today). A useful
LoRA needs on the order of a few hundred rows. The loop to get there: generate_spec.py on
new briefs -> validator -> preview render -> owner approves -> append the row.

Hardware: Unsloth's documented path is NVIDIA/CUDA; AMD Ryzen AI Max+ 395 (ROCm) support
is unverified in this repo (see PR #84 audit). Run on a CUDA box or a rented GPU, then convert
the merged model to GGUF for Ollama/llama-swap on the owner PC.

    python tools/motion_studio/finetune_lora.py --base unsloth/Qwen3-8B --out /models/motion-lora \
        [--epochs 3] [--holdout 0.2]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from generate_spec import SYSTEM  # noqa: E402
import spec as spec_mod  # noqa: E402


def load_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    bad = [r["id"] for r in rows if spec_mod.validate(r["spec"])]
    if bad:
        raise SystemExit(f"invalid specs in dataset (fix before training): {bad}")
    return rows


def to_chat(row: dict) -> list[dict]:
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"BRIEF: {row['brief']}\nFACTS: {json.dumps(row.get('facts') or {}, ensure_ascii=False)}"},
            {"role": "assistant", "content": json.dumps(row["spec"], ensure_ascii=False)}]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="HF id of the base (e.g. an abliterated/instruct model)")
    ap.add_argument("--data", type=Path, default=HERE / "dataset" / "brief_to_spec.jsonl")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--max-seq", type=int, default=8192)
    args = ap.parse_args()
    rows = load_rows(args.data)
    random.Random(7).shuffle(rows)
    k = max(1, int(len(rows) * args.holdout)) if len(rows) > 4 else 0
    held, train = rows[:k], rows[k:]
    if len(train) < 50:
        print(f"WARNING: {len(train)} training rows; expect little or no gain. Grow the dataset first.")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "holdout_ids.json").write_text(json.dumps([r["id"] for r in held]), encoding="utf-8")

    from datasets import Dataset  # heavy imports only when actually training
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel

    model, tok = FastLanguageModel.from_pretrained(args.base, max_seq_length=args.max_seq, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=32, lora_dropout=0.0,
                                             target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                                             "gate_proj", "up_proj", "down_proj"])
    ds = Dataset.from_list([{"text": tok.apply_chat_template(to_chat(r), tokenize=False)} for r in train])
    trainer = SFTTrainer(model=model, tokenizer=tok, train_dataset=ds,
                         args=SFTConfig(output_dir=str(args.out), num_train_epochs=args.epochs,
                                        per_device_train_batch_size=1, gradient_accumulation_steps=4,
                                        learning_rate=2e-4, logging_steps=5, save_strategy="epoch",
                                        dataset_text_field="text", max_seq_length=args.max_seq))
    trainer.train()
    model.save_pretrained(str(args.out / "adapter"))
    tok.save_pretrained(str(args.out / "adapter"))
    print("adapter:", args.out / "adapter", "| evaluate with generate_spec.py --no-example on the holdout briefs")


if __name__ == "__main__":
    main()
