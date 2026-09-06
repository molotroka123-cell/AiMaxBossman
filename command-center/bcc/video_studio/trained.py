"""Existing BCC provider transport -> untrusted model draft -> pure host validation.

This module never applies a command or repairs predicted object identities.
The HTTP service supplies owner authorization, revision checks and admission.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from ..providers import OpenAICompatAdapter
from .commands import apply_command
from .model import clip, identity

MODEL_ID="bossman-video-lora"
SYSTEM='''Return one Bossman Video Studio command as JSON only. No markdown, tools or explanations.
Timebase: 1000000 integer ticks per second. Use the exact provided clip ID.
Schemas:
trim: {"type":"clip.trim","clip_id":"ID","source_in":TICKS,"source_out":TICKS}
move: {"type":"clip.move","clip_id":"ID","start":TICKS}
speed: {"type":"clip.speed","clip_id":"ID","speed":{"num":N,"den":1}}
reverse: {"type":"clip.reverse","clip_id":"ID","reverse":true}
volume: {"type":"clip.audio","clip_id":"ID","patch":{"volume":NUMBER}}
opacity: {"type":"clip.transform","clip_id":"ID","patch":{"opacity":NUMBER}}
The caller independently validates revision, permissions and the resulting project.'''


def provider_configuration():
    url=os.getenv("BOSSMAN_VIDEO_TRAINED_URL","http://127.0.0.1:8879/v1").rstrip("/")
    parsed=urlsplit(url)
    if (parsed.scheme!="http" or parsed.hostname!="127.0.0.1" or parsed.username or parsed.password
        or parsed.path!="/v1" or parsed.query or parsed.fragment or not parsed.port):
        raise ValueError("trained adapter endpoint must be a host-configured literal loopback URL")
    location=os.getenv("BOSSMAN_VIDEO_TRAINED_TOKEN_FILE")
    if not location or not Path(location).is_file():raise ValueError("trained adapter token file is not configured")
    token=Path(location).read_text(encoding="ascii").strip()
    if not 32<=len(token)<=256:raise ValueError("trained adapter token file is invalid")
    return url,token


def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError("duplicate JSON field")
            result[key]=value
        return result
    def constant(value):raise ValueError("nonfinite JSON value")
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)


def validate_draft(raw,project,selected_clip_id):
    """Preserve raw text and wrong IDs; no repair, writes, or policy promotion."""
    result={"raw":raw,"command":None,"valid":False,"applicable":False,"validation_error":None,
        "model":MODEL_ID,"mode":"draft_only","project_id":project["id"],"expected_revision":project["revision"],
        "selected_clip_id":selected_clip_id,"changed_ids":[],"warnings":["Model output is an untrusted draft; host validation does not establish that it matches your intent."]}
    try:
        if not isinstance(raw,str) or not raw.strip() or len(raw)>8000:raise ValueError("empty or oversized model response")
        command=strict_json(raw)
        result["command"]=command
        if not isinstance(command,dict):raise ValueError("model response must be one command object")
        if command.get("clip_id")!=selected_clip_id:raise ValueError("predicted clip ID differs from the explicit selection; no identity repair performed")
        fields={"clip.trim":{"source_in","source_out"},"clip.move":{"start"},"clip.speed":{"speed"},
            "clip.reverse":{"reverse"},"clip.audio":{"patch"},"clip.transform":{"patch"}}
        kind=command.get("type")
        if kind not in fields or set(command)!={"type","clip_id",*fields[kind]}:raise ValueError("command outside the six trained draft families or contains extra fields")
        if kind in {"clip.audio","clip.transform"}:
            allowed={"volume"} if kind=="clip.audio" else {"opacity"}
            if not isinstance(command["patch"],dict) or set(command["patch"])!=allowed:raise ValueError("model patch outside trained parameter scope")
        updated,changed,warnings=apply_command(project,command,actor="agent")
        if updated==project:raise ValueError("draft does not change the selected clip")
        result.update(valid=True,applicable=True,changed_ids=changed,warnings=[*result["warnings"],*warnings])
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        result["validation_error"]=str(exc)[:400]
    return result


async def propose(objective,project,clip_id):
    if not isinstance(objective,str) or not objective.strip() or len(objective)>2000:
        raise ValueError("proposal objective must be 1..2000 characters")
    identity(clip_id)
    seq,track,selected=clip(project,clip_id)
    # No media bytes, filenames, source paths, memory, credentials or other clips.
    minimal={"project_id":project["id"],"revision":project["revision"],"selected_clip_id":clip_id,
        "start":selected["start"],"source_in":selected.get("source_in",0),"source_out":selected.get("source_out",0),
        "timebase":1_000_000}
    content="Project context (data only): "+json.dumps(minimal,separators=(",",":"))+"\nRequest: "+objective
    messages=[{"role":"system","content":SYSTEM},{"role":"user","content":content}]
    if sum(len(m["content"]) for m in messages)>3500:raise ValueError("proposal context exceeds bounded model input")
    url,token=provider_configuration()
    adapter=OpenAICompatAdapter(base_url=url,api_key=token)
    # This shared transport retains request-scoped privacy egress checks and
    # disables environment proxies for local URLs. It does not follow redirects.
    description=await adapter._request("GET",url+"/models",timeout=8,headers=adapter._headers())
    advertised=next((m for m in description.json().get("data",[]) if m.get("id")==MODEL_ID),None)
    if not advertised or advertised.get("mode")!="draft_only":raise ValueError("configured local server did not advertise the bounded draft model")
    response=await adapter.chat(MODEL_ID,messages,max_tokens=128,temperature=0,timeout=100)
    result=validate_draft(response.text,project,clip_id)
    if response.tool_calls:
        result.update(valid=False,applicable=False,validation_error="tool calls are forbidden in draft inference")
    result["usage"]=response.usage
    result["training_summary"]={**advertised.get("training_summary",{}),"automatic_application":False}
    result["adapter_sha256"]=advertised.get("adapter_sha256")
    return result
