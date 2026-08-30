
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel
from .models import JobCreate,JOB_TYPES
from .service import JobService
from .domain import Autopilot
from .bossman_bridge import emit_task
from pathlib import Path
from .ui import app_shell
APP_ID="pc-autopilot-mini"
class Macro(BaseModel):name:str;steps:list[dict]
class Version(BaseModel):steps:list[dict]
class Run(BaseModel):variables:dict={};approve:bool=False
class Repair(BaseModel):failed_step:int;proposal:dict;exchange_root:str|None=None
class Teach(BaseModel): name:str; events:list[dict]
class Trigger(BaseModel): macro_id:str; kind:str; config:dict
class Approve(BaseModel): approve:bool=False
def build_app():
    api=FastAPI(title="PC Autopilot Mini",version="0.7.0");jobs=JobService(APP_ID);eng=Autopilot(jobs.store)
    @api.get("/")
    def root(): return app_shell("PC Autopilot Mini",APP_ID,["Teach from event trace","Macro validation","Versions","Variable expansion","Dry run","Deterministic local executor","Bossman UI handoff","Repair proposals","File triggers","Diff/rollback foundation"])
    @api.get("/health")
    def h():return {"status":"healthy","app":APP_ID,"version":"0.7.0","storage":"sqlite"}
    @api.get("/capabilities")
    def c():return {"standalone":True,"imports_bossman":False,"job_types":JOB_TYPES,
       "features":["macros","versions","validation","dry_run","local_executor","bossman_ui_handoff","repair_proposals"]}
    @api.get("/metrics")
    def m():return jobs.metrics()
    @api.post("/api/jobs")
    def jc(r:JobCreate):
        try:return jobs.create(r)
        except ValueError as e:raise HTTPException(422,str(e))
    @api.get("/api/jobs")
    def jl():return {"jobs":jobs.list()}
    @api.get("/api/jobs/{jid}")
    def jg(jid:str):
        try:return jobs.get(jid)
        except KeyError:raise HTTPException(404,"job not found")
    @api.post("/api/jobs/{jid}/cancel")
    def jcan(jid:str):
        try:return jobs.cancel(jid)
        except KeyError:raise HTTPException(404,"job not found")
    @api.get("/api/jobs/{jid}/artifacts")
    def arts(jid:str):return {"job_id":jid,"artifacts":[]}
    @api.post("/api/macros")
    def cm(r:Macro):
        v=eng.validate_steps(r.steps)
        if not v["valid"]:raise HTTPException(422,v)
        return eng.create_macro(r.name,r.steps)
    @api.get("/api/macros")
    def lm():return {"macros":eng.list()}
    @api.get("/api/macros/{mid}")
    def gm(mid:str):return eng.get(mid)
    @api.post("/api/macros/{mid}/versions")
    def nv(mid:str,r:Version):
        v=eng.validate_steps(r.steps)
        if not v["valid"]:raise HTTPException(422,v)
        return eng.new_version(mid,r.steps)
    @api.post("/api/macros/{mid}/dry-run")
    def dr(mid:str,r:Run):return eng.dry_run(mid,r.variables)
    @api.post("/api/macros/{mid}/run")
    def run(mid:str,r:Run):return eng.run_local(mid,r.variables,r.approve)
    @api.post("/api/macros/{mid}/repair")
    def repair(mid:str,r:Repair):
        prop=eng.repair_proposal(mid,r.failed_step,r.proposal)
        if r.exchange_root:
            p=emit_task(Path(r.exchange_root),"repair_macro",prop,["computer.observe","llm.reasoning"])
            prop["task_file"]=str(p)
        return prop

    @api.post("/api/teach")
    def teach(r:Teach): return eng.teach_from_events(r.name,r.events)
    @api.post("/api/triggers")
    def trigger(r:Trigger): return eng.add_trigger(r.macro_id,r.kind,r.config)
    @api.get("/api/triggers")
    def triggers(): return {"triggers":eng.triggers()}
    @api.get("/api/triggers/evaluate")
    def trigger_eval(): return eng.evaluate_file_triggers()
    @api.get("/api/macros/{mid}/diff")
    def diff(mid:str): return eng.macro_diff(mid)
    @api.post("/api/repairs/{rid}/apply")
    def repair_apply(rid:str,r:Approve):
        try:return eng.repair_apply(rid,r.approve)
        except Exception as e:raise HTTPException(409,str(e))

    @api.get("/api/audit")
    def audit(limit:int=100):return {"events":jobs.store.audit_list(limit)}
    return api
app=build_app()
