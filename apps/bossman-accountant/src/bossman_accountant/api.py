
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from .models import JobCreate, JOB_TYPES
from .service import JobService
from .domain import AccountantEngine
from .ui import app_shell

APP_ID="bossman-accountant"

class TxImport(BaseModel): rows:list[dict]
class WhatIf(BaseModel):
    monthly_revenue_change: float=0
    monthly_cost_change: float=0
class Reconcile(BaseModel): documents:list[dict]

def build_app():
    api=FastAPI(title="Bossman Accountant",version="0.7.0")
    jobs=JobService(APP_ID); eng=AccountantEngine(jobs.store)
    @api.get("/", response_class=None)
    def home(): return app_shell("Bossman Accountant",APP_ID,["CSV/transaction import","Auto-category","P&L and cashflow","Owner digest","Health score","What-if forecast","Reconciliation","Anomaly scan","Period comparison"])
    @api.get("/health")
    def health(): return {"status":"healthy","app":APP_ID,"version":"0.7.0","storage":"sqlite"}
    @api.get("/capabilities")
    def capabilities(): return {"standalone":True,"imports_bossman":False,"job_types":JOB_TYPES,
        "features":["transaction_import","auto_category","pnl","cashflow","owner_digest","health_score","what_if","reconciliation"]}
    @api.get("/metrics")
    def metrics(): return jobs.metrics()
    @api.post("/api/jobs")
    def create_job(req:JobCreate):
        try:return jobs.create(req)
        except ValueError as e:raise HTTPException(422,str(e))
    @api.get("/api/jobs")
    def list_jobs():return {"jobs":jobs.list()}
    @api.get("/api/jobs/{jid}")
    def job(jid:str):
        try:return jobs.get(jid)
        except KeyError:raise HTTPException(404,"job not found")
    @api.post("/api/jobs/{jid}/cancel")
    def cancel(jid:str):
        try:return jobs.cancel(jid)
        except KeyError:raise HTTPException(404,"job not found")
    @api.get("/api/jobs/{jid}/artifacts")
    def artifacts(jid:str): return {"job_id":jid,"artifacts":[]}
    @api.post("/api/transactions/import")
    def tx_import(req:TxImport): return eng.import_transactions(req.rows)
    @api.get("/api/transactions")
    def tx_list(): return {"transactions":eng.list_transactions()}
    @api.get("/api/reports/pnl")
    def pnl(start:str|None=None,end:str|None=None): return eng.pnl(start,end)
    @api.get("/api/reports/cashflow")
    def cashflow(): return eng.cashflow()
    @api.get("/api/reports/owner-digest")
    def digest(): return eng.owner_digest()
    @api.get("/api/reports/health-score")
    def health_score(): return eng.health_score()
    @api.post("/api/forecast/what-if")
    def what_if(req:WhatIf): return eng.what_if(req.monthly_revenue_change,req.monthly_cost_change)
    @api.post("/api/reconcile")
    def reconcile(req:Reconcile): return eng.reconcile(req.documents)

    class CsvImport(BaseModel):
        text:str
        delimiter:str=","
    @api.post("/api/transactions/import-csv")
    def csv_import(req:CsvImport): return eng.import_csv_text(req.text,req.delimiter)
    @api.get("/api/reports/anomalies")
    def anomalies(): return eng.anomalies()
    @api.get("/api/reports/explain-cash")
    def explain_cash(): return eng.explain_cash()
    @api.get("/api/reports/compare")
    def compare(a_start:str,a_end:str,b_start:str,b_end:str): return eng.period_compare(a_start,a_end,b_start,b_end)

    @api.get("/api/audit")
    def audit(limit:int=100): return {"events":jobs.store.audit_list(limit)}
    return api
app=build_app()
