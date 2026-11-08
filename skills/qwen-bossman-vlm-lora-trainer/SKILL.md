---
name: qwen-bossman-vlm-lora-trainer
description: Autonomous Multimodal VLM LoRA Training & 43-Test Verification Workflow for qwen-bossman (Qwen3-VL-8B) on AMD Radeon 8060S ROCm GPU (79.79 GB VRAM) with up to 32K context, Vision UI PC-Control, Bossman Memory (bcc.v2.memory.lessons), Tools/Skills, and Curated Defensive CyberSec.
---

# `qwen-bossman` Multimodal VLM LoRA Training & Verification Skill

Use this skill whenever Bossman needs to train, resume, or verify the **`qwen-bossman`** multimodal Vision-Language model (`Qwen3-VL-8B`, `8.76B` parameters) on the local AMD Strix Halo GPU (`AMD Radeon(TM) 8060S Graphics`, `79.79 GB VRAM`) spending `0.00` Google Colab compute units.

## 1. Base Architecture & Native ROCm Unpacking (`qwen-bossman`)
- **Weights**: `D:\Bossman\openjutsu\models\text_encoders\qwen3vl_8b_int8_convrot.safetensors` (`750/750` exact tensors: `27` Vision Transformer blocks + DeepStack Merger at layers `[8, 16, 24]` + `36` Qwen3-8B language layers).
- **Dequantization**: Un-rotate Hadamard `convrot` (`group_size=256` for language, `16` for vision) via `torch.ops.comfy_kitchen.dequantize_int8_convrot_weight_dtype(t, scale, group_size, 2)` into native `torch.bfloat16`.
- **Mandatory Post-Load Initialization**:
  - Set `cfg.tie_word_embeddings = False` and `cfg.text_config.tie_word_embeddings = False` so `lm_head.weight` is never overwritten by `embed_tokens.weight`.
  - Set `cfg.vision_config.out_hidden_size = 4096` and `cfg.text_config.rope_theta = 5000000.0` (`max_position_embeddings = 262144`).
  - Re-initialize non-persistent rotary buffers on `cuda` after `load_state_dict(..., assign=True)`:
    - `model.model.language_model.rotary_emb = Qwen3VLTextRotaryEmbedding(cfg.text_config, device="cuda")`
    - `model.model.visual.rotary_pos_emb = Qwen3VLVisionRotaryEmbedding((cfg.vision_config.hidden_size // cfg.vision_config.num_heads) // 2).to("cuda")`

## 2. 32K Context Window Support (`32,768` Tokens) on 79.79 GB VRAM
- Enable **Gradient Checkpointing** (`model.gradient_checkpointing_enable()` + `model.enable_input_require_grads()`) and **Chunked Cross-Entropy (`logits_to_keep` / 1024-token chunks)** so `lm_head` never materializes a single `[1, 32768, 151936]` `float32` tensor (`19.9 GB`).
- Use dynamic sequence packing / curriculum up to `max_seq_len = 32768` (`32K`) for long Bossman memory/tool traces and multi-file code/security audits while keeping standard examples unpadded so training speed stays high.

## 3. Dataset Hygiene (Never Train on Raw `.tar.gz` Binaries)
- In `D:\Bossman\datasets\vyber07-cyber-security` (`88.51 GB`), over `80 GB` consists of raw `.tar.gz` source archives (`arvo__...__repo-vul.tar.gz`) and `__dup1` duplicate files.
- Always filter for deduplicated JSON/JSONL records across the 7 defensive topics (`malware`, `botnet-ddos`, `ransomware`, `phishing`, `reverse-engineering`, `security-code`, `vulnerability-intelligence`) + **Bossman Memory (`bcc.v2.memory.lessons`)** + **Bossman Tools & Skills** + **Vision UI Screenshots (`pc.studio`, `AssertFg`, `STOP` guards)**.

## 4. Mandatory Post-Checkpoint Proof Suite (43+ Tests)
Never claim a checkpoint works based on training loss alone. After every major checkpoint:
1. **Tensor Proof**: Verify all `lora_B` matrices have `||lora_B||_F > 0` and zero `NaN`/`Inf`.
2. **Forward-Pass Logit Shift**: Verify `||logits_on - logits_off||_2 > 0` across both Vision+Text (`pixel_values`) and Text prompts, and exact `0.000000` reversibility when `scaling = 0.0`.
3. **Unseen Holdout Perplexity (`test.jsonl`)**: Verify teacher-forced NLL / Perplexity reduction on held-out test records with zero prompt hash overlap.
4. **Generative Rubric (`do_sample=False`)**: Evaluate independent defensive CyberSec (`independent_eval_v1.json`) + Tier 5 Harness + Vision PC-Control (`AssertFg`, `STOP`) and send both the HTML report (`pult_report`) and PNG dashboard (`send_document`) to the Owner's Telegram Pult.
