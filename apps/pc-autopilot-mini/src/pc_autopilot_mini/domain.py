
from __future__ import annotations
from pathlib import Path
import os, shutil, time, uuid

SAFE_LOCAL={"wait","file.copy","file.move","file.mkdir","verify.exists"}
BOSSMAN_UI={"ui.click","ui.type","ui.wait","app.open","browser.download"}
DENY={"payment","security.disable","credential.export"}

class Autopilot:
    def __init__(self,store):self.s=store
    def create_macro(self,name,steps):
        mid=str(uuid.uuid4()); m={"id":mid,"name":name,"version":1,"steps":steps,"status":"draft"}
        self.validate_steps(steps);self.s.kv_put("macros",mid,m);return m
    def validate_steps(self,steps):
        errors=[]
        for i,s in enumerate(steps):
            typ=s.get("action")
            if typ in DENY: errors.append({"step":i,"error":"DENY action"})
            elif typ not in SAFE_LOCAL|BOSSMAN_UI:errors.append({"step":i,"error":"unknown action"})
        return {"valid":not errors,"errors":errors}
    def get(self,mid):return self.s.kv_get("macros",mid)
    def list(self):return [x["value"] for x in self.s.kv_list("macros")]
    def new_version(self,mid,steps):
        old=self.get(mid); self.validate_steps(steps)
        archive=dict(old); self.s.kv_put("macro_versions",f"{mid}:v{old['version']}",archive)
        old["version"]+=1;old["steps"]=steps;old["status"]="draft";self.s.kv_put("macros",mid,old);return old
    def dry_run(self,mid,variables=None):
        m=self.get(mid);variables=variables or {}; plan=[]
        for s in m["steps"]:
            x={k:(self.expand(v,variables) if isinstance(v,str) else v) for k,v in s.items()}
            authority="LOCAL" if x["action"] in SAFE_LOCAL else "BOSSMAN_REQUIRED"
            plan.append({"step":x,"authority":authority})
        return {"macro":mid,"version":m["version"],"plan":plan,"requires_bossman":any(x["authority"]=="BOSSMAN_REQUIRED" for x in plan)}
    def expand(self,v,vars):
        for k,val in vars.items():v=v.replace("{{"+k+"}}",str(val))
        return v
    def run_local(self,mid,variables=None,approve=False):
        plan=self.dry_run(mid,variables)
        if not approve:return {"status":"PREVIEW_ONLY",**plan}
        if plan["requires_bossman"]:return {"status":"PAUSED_NEEDS_BOSSMAN","reason":"UI/computer steps must run through existing Bossman Computer Operator","plan":plan["plan"]}
        run_id=str(uuid.uuid4()); executed=[]
        try:
            for entry in plan["plan"]:
                s=entry["step"];a=s["action"]
                if a=="wait":time.sleep(min(float(s.get("seconds",0)),2))
                elif a=="file.mkdir":Path(s["path"]).mkdir(parents=True,exist_ok=True)
                elif a=="file.copy":shutil.copy2(s["src"],s["dst"])
                elif a=="file.move":shutil.move(s["src"],s["dst"])
                elif a=="verify.exists":
                    if not Path(s["path"]).exists():raise RuntimeError(f"missing: {s['path']}")
                executed.append(s)
            self.s.audit("macro.completed",run_id,{"macro_id":mid,"steps":len(executed)})
            return {"status":"COMPLETED","run_id":run_id,"executed":len(executed)}
        except Exception as e:
            self.s.audit("macro.failed",run_id,{"macro_id":mid,"error":str(e)})
            return {"status":"FAILED","run_id":run_id,"error":str(e),"executed":len(executed)}
    def repair_proposal(self,mid,failed_step,proposal):
        rid=str(uuid.uuid4()); r={"id":rid,"macro_id":mid,"failed_step":failed_step,"proposal":proposal,"status":"PENDING_APPROVAL"}
        self.s.kv_put("repairs",rid,r);return r


    def teach_from_events(self,name,events):
        steps=[]
        for e in events:
            typ=e.get("type")
            if typ=="file_move": steps.append({"action":"file.move","src":e["src"],"dst":e["dst"]})
            elif typ=="file_copy": steps.append({"action":"file.copy","src":e["src"],"dst":e["dst"]})
            elif typ=="mkdir": steps.append({"action":"file.mkdir","path":e["path"]})
            elif typ=="ui_click": steps.append({"action":"ui.click","selector":e.get("selector") or e.get("label","")})
            elif typ=="ui_type": steps.append({"action":"ui.type","selector":e.get("selector",""),"text":e.get("text","")})
            elif typ=="wait": steps.append({"action":"wait","seconds":e.get("seconds",1)})
        return self.create_macro(name,steps)

    def triggers(self): return [x["value"] for x in self.s.kv_list("triggers")]

    def add_trigger(self,macro_id,kind,config):
        self.get(macro_id)
        tid=str(uuid.uuid4()); t={"id":tid,"macro_id":macro_id,"kind":kind,"config":config,"enabled":True}
        self.s.kv_put("triggers",tid,t); return t

    def evaluate_file_triggers(self):
        hits=[]
        for t in self.triggers():
            if not t.get("enabled") or t["kind"]!="file_exists": continue
            p=t["config"].get("path")
            if p and Path(p).exists(): hits.append({"trigger":t,"status":"READY"})
        return {"hits":hits}

    def macro_diff(self,mid):
        cur=self.get(mid); prev_key=f"{mid}:v{max(cur['version']-1,1)}"
        try: prev=self.s.kv_get("macro_versions",prev_key)
        except KeyError: prev=None
        return {"current":cur,"previous":prev}

    def repair_apply(self,repair_id,approve=False):
        r=self.s.kv_get("repairs",repair_id)
        if not approve:return {"status":"PREVIEW_ONLY","repair":r}
        m=self.get(r["macro_id"]); steps=list(m["steps"])
        idx=int(r["failed_step"])
        if idx<0 or idx>=len(steps): raise IndexError(idx)
        steps[idx]=r["proposal"]
        updated=self.new_version(m["id"],steps)
        r["status"]="APPLIED"; self.s.kv_put("repairs",repair_id,r)
        return {"status":"APPLIED","macro":updated}
