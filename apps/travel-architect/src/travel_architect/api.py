
from fastapi import FastAPI,HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from .models import JobCreate,JOB_TYPES
from .service import JobService
from .domain import TravelEngine
from .bossman_bridge import emit_task,resolve_exchange_root
from pathlib import Path
from .ui import app_shell
APP_ID="travel-architect"
# Money fields are finite and non-negative: NaN/Infinity would poison every ranking and make the JSON response a 500.
Money=Field(ge=0,allow_inf_nan=False)
class Trip(BaseModel): origin:list[str]; destination:str; start:str; end:str; travelers:int=1; budget:float|None=Field(default=None,ge=0,allow_inf_nan=False); constraints:dict={}
class Offer(BaseModel): kind:str; provider:str; base_price:float=Money; currency:str="EUR"; fees:dict={}; quality_score:float=Field(default=0,allow_inf_nan=False); url:str|None=None; metadata:dict={}
class Flex(BaseModel): start:str; end:str; flex_days:int=3; durations:list[int]|None=None
class Search(BaseModel): exchange_root:str|None=None
class Watch(BaseModel): max_price:float=Money
def build_app():
    api=FastAPI(title="Travel Architect / Deal Hunter",version="0.7.0");jobs=JobService(APP_ID);eng=TravelEngine(jobs.store)
    @api.exception_handler(RequestValidationError)
    async def _invalid(_req,exc):
        # The default 422 body echoes the rejected input; a NaN/Infinity input cannot be JSON-encoded and became a 500.
        detail=[{"loc":list(e.get("loc",())),"msg":str(e.get("msg","")),"type":str(e.get("type",""))} for e in exc.errors()]
        return JSONResponse(status_code=422,content={"detail":detail})
    @api.get("/")
    def root(): return app_shell("Travel Architect / Deal Hunter",APP_ID,["Trip constraints","True total price","Offer ranking","Flexible date search space","Package vs DIY arbitrage","Smart score","Price watches","Constraint checks","Itinerary skeleton","Bossman browser-search handoff"])
    @api.get("/health")
    def h():return {"status":"healthy","app":APP_ID,"version":"0.7.0","storage":"sqlite"}
    @api.get("/capabilities")
    def c():return {"standalone":True,"imports_bossman":False,"job_types":JOB_TYPES,
      "features":["trip_constraints","true_price","offer_compare","date_arbitrage","package_vs_diy","price_watch","browser_task"]}
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
    @api.post("/api/trips")
    def ct(r:Trip):return eng.create_trip(r.model_dump())
    @api.get("/api/trips")
    def lt():return {"trips":[x["value"] for x in jobs.store.kv_list("trips")]}
    @api.post("/api/trips/{tid}/offers")
    def ao(tid:str,r:Offer):
        try:return eng.add_offer(tid,r.model_dump())
        except ValueError as e:raise HTTPException(422,str(e))
    @api.get("/api/trips/{tid}/compare")
    def cp(tid:str):return eng.compare(tid)
    @api.get("/api/trips/{tid}/package-arbitrage")
    def pa(tid:str):return eng.package_arbitrage(tid)
    @api.post("/api/date-candidates")
    def dc(r:Flex):return {"candidates":eng.date_candidates(r.start,r.end,r.flex_days,r.durations)}
    @api.post("/api/trips/{tid}/search")
    def search(tid:str,r:Search):
        root=None
        if r.exchange_root:
            try:root=resolve_exchange_root(r.exchange_root)
            except ValueError as e:raise HTTPException(400,str(e))
        task=eng.search_task(tid)
        if root is not None:
            p=emit_task(root,task["task_type"],task,["browser.read","llm.reasoning"])
            task["task_file"]=str(p)
        return task
    @api.post("/api/trips/{tid}/watches")
    def watch(tid:str,r:Watch):return eng.create_watch(tid,r.max_price)
    @api.get("/api/watches/evaluate")
    def ew():return eng.evaluate_watches()

    @api.get("/api/trips/{tid}/smart-compare")
    def sc(tid:str): return eng.smart_compare(tid)
    @api.get("/api/trips/{tid}/itinerary")
    def itin(tid:str): return eng.itinerary(tid)
    @api.get("/api/watches")
    def watches(): return eng.watch_snapshot()
    @api.get("/api/trips/{tid}/offers/{oid}/constraint-check")
    def cc(tid:str,oid:str): return eng.constraint_check(tid,oid)

    @api.get("/api/audit")
    def audit(limit:int=100):return {"events":jobs.store.audit_list(limit)}
    return api
app=build_app()
