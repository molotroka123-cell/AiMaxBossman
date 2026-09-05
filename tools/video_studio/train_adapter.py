"""Train a separate LoRA command specialist and compare a fixed held-out suite.

Uses synthetic, domain-validated editing examples; no private media is uploaded.
Base weights stay immutable. An adapter is never automatically promoted by loss.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import random
import time

SYSTEM = '''Return one Bossman Video Studio command as JSON only. No markdown, tools or explanations.
Timebase: 1000000 integer ticks per second. Use the exact provided clip ID.
Schemas:
trim: {"type":"clip.trim","clip_id":"ID","source_in":TICKS,"source_out":TICKS}
move: {"type":"clip.move","clip_id":"ID","start":TICKS}
speed: {"type":"clip.speed","clip_id":"ID","speed":{"num":N,"den":1}}
reverse: {"type":"clip.reverse","clip_id":"ID","reverse":true}
volume: {"type":"clip.audio","clip_id":"ID","patch":{"volume":NUMBER}}
opacity: {"type":"clip.transform","clip_id":"ID","patch":{"opacity":NUMBER}}
The caller independently validates revision, permissions and the resulting project.'''


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def corpus(held_out=False):
    """Templates and clip identity namespaces are disjoint between both splits."""
    values = [1,2,3,4,5,6,7,8] if not held_out else [1.5,2.5,3.5,4.5]
    rows=[]
    for i,value in enumerate(values):
        key=("train_clip_" if not held_out else "unseen_take_")+str(i)
        fraction=round((i%8+1)/10,1)
        seconds=str(value)
        choices=[
            (f'Обрежь клип {key}: от {seconds} до 9 секунд.',
             f'Для {key} оставь исходник в интервале {seconds}–9 секунд.',
             {"type":"clip.trim","clip_id":key,"source_in":int(value*1000000),"source_out":9000000}),
            (f'Перемести клип {key} на {seconds} секунд таймлайна.',
             f'Начало {key} должно быть на отметке {seconds} секунд.',
             {"type":"clip.move","clip_id":key,"start":int(value*1000000)}),
            (f'Ускорь клип {key} в {i%3+2} раза.',
             f'Задай скорость воспроизведения {i%3+2}x для {key}.',
             {"type":"clip.speed","clip_id":key,"speed":{"num":i%3+2,"den":1}}),
            (f'Разверни воспроизведение клипа {key} назад.',
             f'Нужен reverse для {key}, показывай его с конца.',
             {"type":"clip.reverse","clip_id":key,"reverse":True}),
            (f'Установи громкость клипа {key} равной {fraction}.',
             f'Поставь линейный уровень звука {fraction} у {key}.',
             {"type":"clip.audio","clip_id":key,"patch":{"volume":fraction}}),
            (f'Установи непрозрачность клипа {key} равной {fraction}.',
             f'Измени opacity {key} на {fraction}.',
             {"type":"clip.transform","clip_id":key,"patch":{"opacity":fraction}}),
        ]
        for a,b,command in choices:
            rows.append({"input":b if held_out else a,"command":command})
    return rows


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--base",default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--steps",type=int,default=120)
    parser.add_argument("--seed",type=int,default=43)
    args=parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Use a new output directory; previous training evidence is immutable")
    args.output.mkdir(parents=True)
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import LoraConfig, get_peft_model
    from huggingface_hub import model_info
    random.seed(args.seed);torch.manual_seed(args.seed)
    if not torch.cuda.is_available():
        raise RuntimeError("This bounded training recipe requires CUDA; no silent unbounded CPU training")
    torch.cuda.set_per_process_memory_fraction(.45)
    torch.set_num_threads(4)
    train,holdout=corpus(),corpus(True)
    train_hash=hashlib.sha256(canonical(train).encode()).hexdigest()
    suite_hash=hashlib.sha256(canonical(holdout).encode()).hexdigest()
    (args.output/"train.json").write_text(canonical(train),encoding="utf-8")
    (args.output/"holdout.json").write_text(canonical(holdout),encoding="utf-8")
    revision=model_info(args.base).sha
    print(canonical({"stage":"loading","base":args.base,"revision":revision,"train":len(train),"holdout":len(holdout)}),flush=True)
    tokenizer=AutoTokenizer.from_pretrained(args.base,revision=revision,trust_remote_code=False)
    tokenizer.pad_token=tokenizer.eos_token
    base=AutoModelForCausalLM.from_pretrained(args.base,revision=revision,trust_remote_code=False,
                dtype=torch.bfloat16,attn_implementation="sdpa").to("cuda")
    def prompt(row):
        return tokenizer.apply_chat_template([{"role":"system","content":SYSTEM},{"role":"user","content":row["input"]}],
                                              tokenize=False,add_generation_prompt=True)
    def evaluate(model,stage):
        model.eval();model.config.use_cache=True
        results=[]
        for i,row in enumerate(holdout):
            encoded=tokenizer(prompt(row),return_tensors="pt").to("cuda")
            with torch.inference_mode():
                output=model.generate(**encoded,max_new_tokens=112,do_sample=False,pad_token_id=tokenizer.eos_token_id)
            text=tokenizer.decode(output[0,encoded.input_ids.shape[1]:],skip_special_tokens=True).strip()
            try: predicted=json.loads(text)
            except ValueError: predicted=None
            results.append({"case":i,"expected":row["command"],"predicted":predicted,
                            "exact":predicted==row["command"],"json_valid":isinstance(predicted,dict)})
            print(canonical({"stage":stage,"case":i,"exact":results[-1]["exact"]}),flush=True)
        return {"suite_sha256":suite_hash,"total":len(results),"exact":sum(r["exact"] for r in results),
                "json_valid":sum(r["json_valid"] for r in results),"cases":results}
    start=time.perf_counter()
    baseline=evaluate(base,"baseline")
    (args.output/"baseline.json").write_text(canonical(baseline),encoding="utf-8")
    model=get_peft_model(base,LoraConfig(r=16,lora_alpha=32,lora_dropout=.05,
        target_modules=["q_proj","v_proj","k_proj","o_proj"],task_type="CAUSAL_LM"))
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model.config.use_cache=False
    model.train()
    optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=2e-4)
    encoded_rows=[]
    for row in train:
        prefix=prompt(row)
        full=prefix+canonical(row["command"])+tokenizer.eos_token
        encoded=tokenizer(full,return_tensors="pt")
        labels=encoded.input_ids.clone()
        prefix_length=len(tokenizer(prefix).input_ids)
        labels[:,:prefix_length]=-100
        encoded_rows.append((encoded,labels))
    losses=[]
    for step in range(args.steps):
        encoded,labels=random.choice(encoded_rows)
        optimizer.zero_grad(set_to_none=True)
        output=model(**{k:v.to("cuda") for k,v in encoded.items()},labels=labels.to("cuda"))
        loss=output.loss
        if not torch.isfinite(loss): raise RuntimeError("Nonfinite loss; candidate not eligible")
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(),1.0)
        optimizer.step()
        losses.append(float(loss.detach()))
        if step%10==0 or step==args.steps-1:
            print(canonical({"stage":"train","step":step+1,"loss":losses[-1],"allocated_mib":round(torch.cuda.memory_allocated()/2**20)}),flush=True)
    del optimizer
    model.save_pretrained(args.output/"adapter",safe_serialization=True)
    tokenizer.save_pretrained(args.output/"adapter")
    candidate=evaluate(model,"candidate")
    (args.output/"candidate.json").write_text(canonical(candidate),encoding="utf-8")
    preserved=all(not b["exact"] or c["exact"] for b,c in zip(baseline["cases"],candidate["cases"]))
    report={"base":args.base,"base_revision":revision,"method":"LoRA", "weights_trained":True,
            "train_sha256":train_hash,"suite_sha256":suite_hash,"steps":args.steps,"seed":args.seed,
            "baseline_exact":baseline["exact"],"candidate_exact":candidate["exact"],"holdout_total":len(holdout),
            "preserved_baseline_passes":preserved,"eligible_for_review":preserved and candidate["exact"]>baseline["exact"],
            "activated":False,"scope":"six editing command families; unseen templates and IDs; no general intelligence claim",
            "seconds":round(time.perf_counter()-start,2),"max_gpu_mib":round(torch.cuda.max_memory_allocated()/2**20),
            "first_loss":losses[0],"last_loss":losses[-1],"torch":torch.__version__}
    (args.output/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(canonical(report),flush=True)


if __name__=="__main__":
    main()
