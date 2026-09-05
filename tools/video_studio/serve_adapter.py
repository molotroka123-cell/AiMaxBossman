"""Authenticated loopback-only draft inference for an explicitly trained LoRA.

No tools, subprocesses or project/filesystem commands are exposed to the model.
All model assets must already be cached; serving never downloads base weights.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

MODEL_ID = "bossman-video-lora"
MAX_INPUT = 3500
MAX_OUTPUT = 128


def digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):h.update(block)
    return h.hexdigest()


class Runtime:
    def __init__(self, adapter, report, gpu_mib=2048):
        self.adapter=Path(adapter).resolve();self.report_path=Path(report).resolve()
        if not self.report_path.is_file():raise ValueError("training report is missing")
        self.report=json.loads(self.report_path.read_text(encoding="utf-8"))
        if (self.report.get("weights_trained") is not True or self.report.get("method")!="LoRA"
            or self.report.get("base")!="Qwen/Qwen2.5-0.5B-Instruct"
            or not re.fullmatch(r"[a-f0-9]{40}",self.report.get("base_revision",""))):
            raise ValueError("server requires the reviewed trained LoRA and immutable base revision")
        self.weights=self.adapter/"adapter_model.safetensors"
        configuration=self.adapter/"adapter_config.json"
        if not self.weights.is_file() or not configuration.is_file():raise ValueError("adapter safetensors/configuration are missing")
        config=json.loads(configuration.read_text(encoding="utf-8"))
        if config.get("base_model_name_or_path")!=self.report["base"] or config.get("peft_type")!="LORA" or config.get("task_type")!="CAUSAL_LM":
            raise ValueError("adapter and reviewed report disagree")
        if type(gpu_mib) is not int or not 512<=gpu_mib<=3072:raise ValueError("GPU cap must be 512..3072 MiB")
        self.gpu_mib=gpu_mib
        self.asset_digests={p:digest(p) for p in [self.weights,configuration,self.report_path]}
        self.identity=self.asset_digests[self.weights]
        self.model=None;self.tokenizer=None
        self.inference_lock=threading.Lock()
        self.last_used=0.0

    def load(self):
        if self.model is not None:return
        if any(digest(path)!=expected for path,expected in self.asset_digests.items()):raise ValueError("model assets changed after server initialization")
        os.environ["HF_HUB_OFFLINE"]="1"
        os.environ["TRANSFORMERS_OFFLINE"]="1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"
        import torch
        from transformers import AutoModelForCausalLM,AutoTokenizer
        from peft import PeftModel
        if not torch.cuda.is_available():raise ValueError("configured adapter server requires CUDA; no silent CPU fallback")
        total=torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(min(.4,self.gpu_mib*2**20/total))
        torch.set_num_threads(4)
        tokenizer=AutoTokenizer.from_pretrained(self.report["base"],revision=self.report["base_revision"],trust_remote_code=False,local_files_only=True)
        base=AutoModelForCausalLM.from_pretrained(self.report["base"],revision=self.report["base_revision"],trust_remote_code=False,
            local_files_only=True,use_safetensors=True,dtype=torch.bfloat16,attn_implementation="sdpa")
        model=PeftModel.from_pretrained(base,str(self.adapter),is_trainable=False,local_files_only=True).to("cuda")
        model.eval();model.config.use_cache=True
        self.tokenizer=tokenizer;self.model=model

    def generate(self, messages, max_tokens, stop_event):
        import torch
        from transformers import StoppingCriteria,StoppingCriteriaList
        class Cancelled(StoppingCriteria):
            def __call__(self,input_ids,scores,**kwargs):return stop_event.is_set()
        with self.inference_lock:
            self.load()
            if stop_event.is_set():raise TimeoutError("request cancelled")
            rendered=self.tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
            encoded=self.tokenizer(rendered,return_tensors="pt").to("cuda")
            count=encoded.input_ids.shape[1]
            if count>3500:raise ValueError("input token budget exceeded")
            with torch.inference_mode():
                output=self.model.generate(**encoded,max_new_tokens=max_tokens,do_sample=False,
                    stopping_criteria=StoppingCriteriaList([Cancelled()]),pad_token_id=self.tokenizer.eos_token_id)
            tokens=output.shape[1]-count
            text=self.tokenizer.decode(output[0,count:],skip_special_tokens=True)
            self.last_used=time.monotonic()
            return text,count,tokens

    def unload_if_idle(self, seconds=120):
        if self.model is not None and time.monotonic()-self.last_used>seconds and self.inference_lock.acquire(blocking=False):
            try:
                import torch
                self.model=None;self.tokenizer=None
                import gc
                gc.collect();torch.cuda.empty_cache()
            finally:self.inference_lock.release()


def token_file(path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,"w",encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(32));stream.flush();os.fsync(stream.fileno())
    token=path.read_text(encoding="ascii").strip()
    if len(token)<32 or len(token)>256:raise ValueError("invalid local server token file")
    return token


def validate_request(data):
    if not isinstance(data,dict) or set(data)-{"model","messages","max_tokens","temperature","top_p","stream","response_format"}:
        raise ValueError("unsupported request fields; tools are not available")
    if data.get("model")!=MODEL_ID or data.get("stream",False) is not False:raise ValueError("unknown model or unsupported streaming")
    messages=data.get("messages")
    if not isinstance(messages,list) or not 1<=len(messages)<=8:raise ValueError("messages must be a bounded list")
    for item in messages:
        if not isinstance(item,dict) or set(item)!={"role","content"} or item["role"] not in {"system","user","assistant"} or not isinstance(item["content"],str):
            raise ValueError("messages accept plain text only")
    if sum(len(item["content"]) for item in messages)>MAX_INPUT:raise ValueError("input exceeds 3500 characters")
    maximum=data.get("max_tokens",MAX_OUTPUT)
    if type(maximum) is not int or not 1<=maximum<=MAX_OUTPUT:raise ValueError("output must be 1..128 tokens")
    return messages,maximum


def create_app(runtime, token):
    from fastapi import FastAPI,Request
    from fastapi.responses import JSONResponse
    from contextlib import asynccontextmanager
    gate=asyncio.Lock()
    @asynccontextmanager
    async def lifespan(app):
        async def cleanup():
            while True:
                await asyncio.sleep(30)
                await asyncio.to_thread(runtime.unload_if_idle)
        cleaner=asyncio.create_task(cleanup())
        yield
        cleaner.cancel()
        await asyncio.gather(cleaner,return_exceptions=True)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    @app.middleware("http")
    async def authorization(request,call_next):
        if request.client and request.client.host not in {"127.0.0.1","::1","testclient"}:
            return JSONResponse({"error":"loopback only"},status_code=403)
        if request.headers.get("origin"):
            return JSONResponse({"error":"browser origins are not accepted"},status_code=403)
        if not secrets.compare_digest(request.headers.get("authorization",""),"Bearer "+token):
            return JSONResponse({"error":"authentication required"},status_code=401)
        return await call_next(request)
    @app.get("/v1/models")
    async def models():
        return {"object":"list","data":[{"id":MODEL_ID,"object":"model","owned_by":"local-trained-adapter",
            "mode":"draft_only","adapter_sha256":runtime.identity,"loaded":runtime.model is not None,
            "training_summary":{k:runtime.report.get(k) for k in ["baseline_exact","candidate_exact","holdout_total","suite_sha256","scope"]}}]}
    # Use a concrete runtime annotation; FastAPI resolves nested future annotations
    # through module globals, while Request is intentionally an optional dependency.
    globals()["Request"]=Request
    @app.post("/v1/chat/completions")
    async def completion(request: Request):
        payload=bytearray()
        async for block in request.stream():
            if len(payload)+len(block)>20000:return JSONResponse({"error":"request body too large"},status_code=413)
            payload.extend(block)
        try:messages,maximum=validate_request(json.loads(payload))
        except (ValueError,TypeError):return JSONResponse({"error":"invalid bounded draft request"},status_code=400)
        try:await asyncio.wait_for(gate.acquire(),timeout=2)
        except asyncio.TimeoutError:return JSONResponse({"error":"local inference busy"},status_code=429)
        stop=threading.Event();worker=None
        try:
            worker=asyncio.create_task(asyncio.to_thread(runtime.generate,messages,maximum,stop))
            text,prompt_tokens,output_tokens=await asyncio.wait_for(asyncio.shield(worker),timeout=90)
            return {"id":"local-"+secrets.token_hex(8),"object":"chat.completion","created":int(time.time()),"model":MODEL_ID,
                "choices":[{"index":0,"message":{"role":"assistant","content":text},"finish_reason":"length" if output_tokens>=maximum else "stop"}],
                "usage":{"prompt_tokens":prompt_tokens,"completion_tokens":output_tokens,"total_tokens":prompt_tokens+output_tokens},
                "mode":"draft_only","adapter_sha256":runtime.identity}
        except asyncio.CancelledError:
            stop.set()
            if worker:await asyncio.gather(worker,return_exceptions=True)
            raise
        except Exception:
            stop.set()
            if worker:await asyncio.gather(worker,return_exceptions=True)
            return JSONResponse({"error":"local draft inference unavailable; no fallback used"},status_code=503)
        finally:gate.release()
    return app


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--adapter",type=Path,required=True);parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--port",type=int,default=8879);parser.add_argument("--token-file",type=Path)
    parser.add_argument("--gpu-mib",type=int,default=2048)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error("port must be 1024..65535")
    runtime=Runtime(args.adapter,args.report,args.gpu_mib)
    token=token_file(args.token_file or args.report.parent/"server.token")
    import uvicorn
    uvicorn.run(create_app(runtime,token),host="127.0.0.1",port=args.port,access_log=False,proxy_headers=False,limit_concurrency=4)


if __name__=="__main__":main()
