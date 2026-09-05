"""Evidence-linked local retrieval, B-roll candidates and measured duplicates.

BM25 searches caption text and owner-provided media metadata. It is not visual
scene understanding. Near-duplicate measurements use sampled decoded pixels and
are review candidates, not proof that unsampled frames/audio are identical.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

from .media import MediaLibrary,binary,blocking,input_args,process
from .model import TICKS,validate_project

STOP=set("the a an is are of for to in on and or find show please moment where with найди найти покажи момент где с в на и или из для этот эту это видео ролик".split())


def _tokens(text):
    return [t for t in re.findall(r"[\w]+",text.casefold(),re.UNICODE) if len(t)>1 and t not in STOP]


def search_project(project,query,limit=10,metadata_only=False):
    validate_project(project)
    if not isinstance(query,str) or not 1<=len(query)<=2000:raise ValueError("Search query must contain 1..2000 characters")
    if type(limit) is not int or not 1<=limit<=50:raise ValueError("Search result limit must be 1..50")
    documents=[]
    for media in project["media"].values():
        text=" ".join([media["name"],str(media.get("folder","")),*map(str,media.get("tags",[]))])
        documents.append({"media_id":media["id"],"text":text,"evidence":"media_metadata","start":None,"end":None})
    if not metadata_only:
        for cue in project.get("captions",[]):
            documents.append({"caption_id":cue.get("id"),"sequence_id":cue.get("sequence_id",project["active_sequence_id"]),
                "start":cue["start"],"end":cue["end"],"text":cue["text"],"evidence":"caption_text"})
    terms=set(_tokens(query));counts=[Counter(_tokens(d["text"])) for d in documents]
    average=sum(sum(c.values()) for c in counts)/max(1,len(counts)) or 1
    frequency={t:sum(t in c for c in counts) for t in terms}
    hits=[]
    for doc,counter in zip(documents,counts):
        matched=terms&counter.keys()
        if not matched:continue
        score=0
        for term in matched:
            idf=math.log(1+(len(documents)-frequency[term]+.5)/(frequency[term]+.5))
            tf=counter[term];norm=1.2*(.25+.75*sum(counter.values())/average)
            score+=idf*(tf*2.2)/(tf+norm)
        hits.append({**doc,"score":round(score,8),"matched_terms":sorted(matched)})
    hits.sort(key=lambda h:(-h["score"],str(h.get("media_id",h.get("caption_id","")))))
    return {"matches":hits[:limit],"query":query,"method":"BM25(k1=1.2,b=0.75)","local":True,
        "egress":"none","estimated_cost_usd":0,"visual_understanding":False,
        "warnings":["Search covers captions and names/tags; unseen visual content is not inferred."]}


def suggest_broll(project,objective,exclude_media_ids=None,limit=5):
    result=search_project(project,objective,50,metadata_only=True)
    excluded=set(exclude_media_ids or [])
    if not excluded<=project["media"].keys():raise ValueError("Excluded media ID is not part of the project")
    matches=[hit for hit in result["matches"] if hit["media_id"] not in excluded
             and project["media"][hit["media_id"]].get("has_video")]
    return {**result,"matches":matches[:limit],"draft_only":True,
        "reason":"Existing imported assets ranked by matching names/tags; no stock download or generated asset",
        "next_action":None if matches else "Import or tag relevant B-roll; no matching existing asset was found"}


async def find_duplicates(project,root,progress=None):
    validate_project(project)
    media=list(project["media"].values())
    if len(media)>200:raise ValueError("Duplicate analysis is bounded to 200 assets per project")
    library=MediaLibrary(root);root=Path(root).resolve();samples={};matches=[]
    for index,m in enumerate(media):
        path=await blocking(library.resolve,m)
        if m.get("has_video") and m["duration_ticks"]>0:
            cache=root/"cache"/"duplicates";cache.mkdir(parents=True,exist_ok=True)
            target=cache/(m["sha256"]+"-rgb16x16-8-v1.bin")
            if target.is_file() and 768<=target.stat().st_size<=8*768:
                raw=target.read_bytes()
            else:
                hz=8*TICKS/m["duration_ticks"]
                raw,_=await process([binary("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin",*input_args(path),
                    "-vf",f"fps={hz:.12f},scale=16:16","-frames:v","8","-pix_fmt","rgb24","-an","-f","rawvideo","pipe:1"],
                    binary_output=True,max_output=8*768,timeout=120)
                if len(raw)<768 or len(raw)%768:raise ValueError("Duplicate sampler did not return complete frames")
                temporary=target.with_suffix(".tmp-"+hashlib.sha256(str(id(raw)).encode()).hexdigest()[:12])
                try:
                    temporary.write_bytes(raw);temporary.replace(target)
                finally:temporary.unlink(missing_ok=True)
            samples[m["id"]]=raw
        if progress:await progress("duplicate_sampling",{"assets":index+1,"total":len(media)})
    for index,a in enumerate(media):
        for b in media[index+1:]:
            exact=a["sha256"]==b["sha256"]
            left,right=samples.get(a["id"]),samples.get(b["id"])
            if exact:
                matches.append({"media_ids":[a["id"],b["id"]],"kind":"exact_content_sha256","distance":0})
            elif (left and right and len(left)==len(right) and
                  abs(a["duration_ticks"]-b["duration_ticks"])<=50000):
                distance=math.sqrt(sum((x-y)**2 for x,y in zip(left,right))/len(left))/255
                if distance<=.025:
                    matches.append({"media_ids":[a["id"],b["id"]],"kind":"sampled_visual_candidate",
                        "distance":round(distance,8),"samples":len(left)//768,
                        "interval":{"start":0,"end":min(a["duration_ticks"],b["duration_ticks"])}})
    return {"matches":matches,"local":True,"egress":"none","estimated_cost_usd":0,"draft_only":True,
        "warnings":["Near matches compare 8 sampled frames, not all frames or audio. No source was deleted."]}
