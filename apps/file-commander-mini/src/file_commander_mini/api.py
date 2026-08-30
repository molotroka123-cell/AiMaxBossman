
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel
from .models import JobCreate,JOB_TYPES
from .service import JobService
from .domain import FileCommander
from .ui import app_shell
APP_ID="file-commander-mini"
class RootReq(BaseModel): root:str
class ApplyReq(BaseModel): operations:list[dict]; approve:bool=False
class UndoReq(BaseModel): approve:bool=False
class RenameReq(BaseModel): root:str; pattern:str="{stem}"; replace_spaces:bool=True
class RuleReq(BaseModel): name:str; match_ext:list[str]; target_dir:str
def build_app():
    api=FastAPI(title="File Commander Mini",version="0.7.0"); jobs=JobService(APP_ID); eng=FileCommander(jobs.store)
    @api.get("/")
    def home(): return app_shell("File Commander Mini",APP_ID,["Disk scan","Duplicate detection","Cleanup summary","Organize/rename plans","Rules","Preview-first apply","Undo/rollback","Project grouping"])
    @api.get("/health")
    def health():return {"status":"healthy","app":APP_ID,"version":"0.7.0","storage":"sqlite"}
    @api.get("/capabilities")
    def caps():return {"standalone":True,"imports_bossman":False,"job_types":JOB_TYPES,"features":["scan","duplicates","organize_plan","safe_apply","undo"]}
    @api.get("/metrics")
    def metrics():return jobs.metrics()
    @api.post("/api/jobs")
    def create(req:JobCreate):
        try:return jobs.create(req)
        except ValueError as e:raise HTTPException(422,str(e))
    @api.get("/api/jobs")
    def jl():return {"jobs":jobs.list()}
    @api.get("/api/jobs/{jid}")
    def jg(jid:str):
        try:return jobs.get(jid)
        except KeyError:raise HTTPException(404,"job not found")
    @api.post("/api/jobs/{jid}/cancel")
    def jc(jid:str):
        try:return jobs.cancel(jid)
        except KeyError:raise HTTPException(404,"job not found")
    @api.get("/api/jobs/{jid}/artifacts")
    def ja(jid:str):return {"job_id":jid,"artifacts":[]}
    @api.post("/api/files/scan")
    def scan(r:RootReq):
        try:return eng.scan(r.root)
        except (PermissionError,FileNotFoundError) as e:raise HTTPException(403,str(e))
    @api.post("/api/files/duplicates")
    def dup(r:RootReq):
        try:return eng.duplicates(r.root)
        except PermissionError as e:raise HTTPException(403,str(e))
    @api.post("/api/files/organize-plan")
    def plan(r:RootReq):return eng.organize_plan(r.root)
    @api.post("/api/files/apply")
    def apply(r:ApplyReq):
        try:return eng.apply(r.operations,r.approve)
        except Exception as e:raise HTTPException(409,str(e))
    @api.post("/api/files/undo/{batch_id}")
    def undo(batch_id:str,r:UndoReq):
        try:return eng.undo(batch_id,r.approve)
        except Exception as e:raise HTTPException(409,str(e))

    @api.post("/api/files/cleanup-summary")
    def cleanup(r:RootReq): return eng.cleanup_summary(r.root)
    @api.post("/api/files/rename-plan")
    def rename_plan(r:RenameReq): return eng.rename_plan(r.root,r.pattern,r.replace_spaces)
    @api.post("/api/rules")
    def add_rule(r:RuleReq): return eng.save_rule(r.name,r.match_ext,r.target_dir)
    @api.get("/api/rules")
    def rules(): return {"rules":[x["value"] for x in jobs.store.kv_list("rules")]}
    @api.post("/api/files/rule-plan")
    def rule_plan(r:RootReq): return eng.rule_plan(r.root)
    @api.post("/api/files/project-groups")
    def project_groups(r:RootReq): return eng.project_groups(r.root)

    @api.get("/api/audit")
    def audit(limit:int=100):return {"events":jobs.store.audit_list(limit)}
    return api
app=build_app()
