"""Optional local caption translation: bounded CPU worker, fixed language pair.

Models/interpreters are chosen by host configuration. Task arguments cannot choose
a URL, executable or arbitrary model. Generation changes text only, never IDs/time.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

MODEL="Helsinki-NLP/opus-mt-en-ru"
REVISION="bb09c99d180016eac6819df3dae68edb1690fdee"
WEIGHTS_SHA256="d15fa58c6bc3efd3629c1b6b86d9aa6d15d2751a4620aa4cdd7eed7b5cbe583b"


def _validate(captions,source,target):
    if (source,target)!=("en","ru"):
        raise ValueError("Installed translation model supports English → Russian only; import a translated SRT/VTT for another pair")
    if not isinstance(captions,list) or not 1<=len(captions)<=1000:
        raise ValueError("Translation requires 1..1000 caption cues")
    total=0
    for cue in captions:
        if not isinstance(cue,dict) or not isinstance(cue.get("text"),str) or not 1<=len(cue["text"])<=800:
            raise ValueError("Each translation cue must contain 1..800 characters")
        if (type(cue.get("start")) is not int or type(cue.get("end")) is not int
                or not 0<=cue["start"]<cue["end"]<=604800000000):
            raise ValueError("Invalid caption time range")
        total+=len(cue["text"])
    if total>128000:raise ValueError("Translation text exceeds the bounded 128k character batch")
    json.dumps(captions,allow_nan=False)


def _model_identity(path):
    folder=Path(path).resolve()
    weight=folder/"pytorch_model.bin"
    if not weight.is_file() or not (folder/"config.json").is_file():
        raise ValueError("Local translation model is missing; install the documented pinned Marian weights")
    digest=hashlib.sha256()
    with weight.open("rb") as file:
        while chunk:=file.read(1024*1024):digest.update(chunk)
    if digest.hexdigest()!=WEIGHTS_SHA256:
        raise ValueError("Translation weights differ from the verified pinned model")
    # Tokenizer/config edits must also invalidate cached translated text.
    for name in ("config.json","generation_config.json","source.spm","target.spm","tokenizer_config.json","vocab.json"):
        digest.update(name.encode())
        with (folder/name).open("rb") as file:
            while chunk:=file.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()


async def translate_captions(captions,root,model_path,python_executable,source="en",target="ru",progress=None):
    from .media import process, blocking
    _validate(captions,source,target)
    if not python_executable or not Path(python_executable).is_file():
        raise ValueError("Local translation Python runtime is not configured")
    if not model_path:raise ValueError("Local translation model is not configured")
    identity=await blocking(_model_identity,model_path)
    request={"captions":captions,"source":source,"target":target,"model_sha256":identity,"recipe":1}
    key=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    cache=Path(root).resolve()/"cache"/"translation";cache.mkdir(parents=True,exist_ok=True)
    result_path=cache/(key+".json")
    if result_path.is_file():
        result=json.loads(result_path.read_text(encoding="utf-8"));result["cached"]=True
        _validate(result["captions"],source,target)
        return result
    if progress:await progress("translating",{"cues":len(captions),"local":True})
    with tempfile.TemporaryDirectory(prefix="translation-",dir=cache) as temporary:
        source_path=Path(temporary)/"input.json";output=Path(temporary)/"output.json"
        source_path.write_text(json.dumps(request,ensure_ascii=False),encoding="utf-8")
        await process([python_executable,Path(__file__).resolve(),"--input",source_path,"--output",output,
                       "--model",Path(model_path).resolve()],timeout=900)
        result=json.loads(output.read_text(encoding="utf-8"))
        _validate(result["captions"],source,target)
        if len(result["captions"])!=len(captions) or any(
            {k:v for k,v in before.items() if k!="text"}!={k:v for k,v in after.items() if k!="text"}
            for before,after in zip(captions,result["captions"])):
            raise ValueError("Translation altered caption identity or timing")
        result.update(local=True,egress="none",estimated_cost_usd=0,cached=False,draft_only=True,
            model=MODEL,revision=REVISION,model_sha256=identity,source=source,target=target,
            warnings=["Machine translation needs language review; no caption was automatically applied."])
        output.write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")
        try:os.link(output,result_path)
        except FileExistsError:pass
    if progress:await progress("translation_complete",{"cues":len(captions)})
    return result


def _worker(input_path,output_path,model_path):
    os.environ["HF_HUB_OFFLINE"]="1";os.environ["TRANSFORMERS_OFFLINE"]="1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"
    from transformers import MarianTokenizer, MarianMTModel
    import torch
    torch.set_num_threads(4)
    data=json.loads(Path(input_path).read_text(encoding="utf-8"))
    _validate(data["captions"],data["source"],data["target"])
    if _model_identity(model_path)!=data["model_sha256"]:raise ValueError("Translation weights changed")
    tokenizer=MarianTokenizer.from_pretrained(model_path,local_files_only=True)
    # No pickle code execution: modern Torch restricted weight loader; no custom
    # model code from the repository. Runtime only reads installed local files.
    model=MarianMTModel.from_pretrained(model_path,local_files_only=True,weights_only=True).eval().to("cpu")
    result=deepcopy(data["captions"])
    with torch.inference_mode():
        for cue in result:
            encoded=tokenizer(cue["text"],return_tensors="pt",truncation=False)
            if encoded["input_ids"].shape[1]>256:raise ValueError("Caption exceeds 256 translation tokens; split the cue")
            tokens=model.generate(**encoded,max_new_tokens=256,num_beams=4,do_sample=False)
            if tokens.shape[1]>=256 and tokens[0,-1].item()!=tokenizer.eos_token_id:
                raise ValueError("Translation output was truncated")
            cue["text"]=tokenizer.decode(tokens[0],skip_special_tokens=True).strip()
            if not cue["text"]:raise ValueError("Translation returned empty text")
    Path(output_path).write_text(json.dumps({"captions":result},ensure_ascii=False),encoding="utf-8")


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--input",required=True)
    parser.add_argument("--output",required=True);parser.add_argument("--model",required=True)
    args=parser.parse_args();_worker(args.input,args.output,args.model)
