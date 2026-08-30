
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel
from .models import JobCreate,JOB_TYPES
from .service import JobService
from .domain import ExamEngine
from .bossman_bridge import emit_task
from .ui import app_shell
APP_ID="exam-trainer-ai"
class ExamCreate(BaseModel): name:str; language:str="en"; topics:list[dict]
class Questions(BaseModel): questions:list[dict]
class Answers(BaseModel): answers:dict[str,str]
class Mastery(BaseModel): mastery:dict[str,float]={}; count:int=10
class Collect(BaseModel): query:str; exchange_root:str|None=None
class SessionCreate(BaseModel): learner_id:str; mode:str="adaptive"; language:str|None=None
class AnswerSubmit(BaseModel): question_id:str; answer:str
class MockReq(BaseModel): count:int=20
def build_app():
    api=FastAPI(title="Exam Trainer AI",version="0.7.0"); jobs=JobService(APP_ID); eng=ExamEngine(jobs.store)
    @api.get("/")
    def home(): return app_shell("Exam Trainer AI",APP_ID,["Exam blueprint","Question bank","Diagnostics","Adaptive priority","Mock exams","Readiness score","Training sessions","Multilingual session metadata","Source provenance","Bossman browser collection handoff"])
    @api.get("/health")
    def h():return {"status":"healthy","app":APP_ID,"version":"0.7.0","storage":"sqlite"}
    @api.get("/capabilities")
    def c():return {"standalone":True,"imports_bossman":False,"job_types":JOB_TYPES,
      "features":["exam_blueprint","question_bank","diagnostic","probability_map","adaptive_priority","training_sets","readiness","bossman_browser_task"]}
    @api.get("/metrics")
    def m():return jobs.metrics()
    @api.post("/api/jobs")
    def jc(req:JobCreate):
        try:return jobs.create(req)
        except ValueError as e:raise HTTPException(422,str(e))
    @api.get("/api/jobs")
    def jl():return {"jobs":jobs.list()}
    @api.get("/api/jobs/{jid}")
    def jg(jid:str):
        try:return jobs.get(jid)
        except KeyError:raise HTTPException(404,"job not found")
    @api.post("/api/jobs/{jid}/cancel")
    def jcancel(jid:str):
        try:return jobs.cancel(jid)
        except KeyError:raise HTTPException(404,"job not found")
    @api.get("/api/jobs/{jid}/artifacts")
    def arts(jid:str):return {"job_id":jid,"artifacts":[]}
    @api.post("/api/exams")
    def ce(r:ExamCreate):return eng.create_exam(r.name,r.language,r.topics)
    @api.get("/api/exams")
    def le():return {"exams":[x["value"] for x in jobs.store.kv_list("exams")]}
    @api.post("/api/exams/{eid}/questions")
    def aq(eid:str,r:Questions):return {"questions":eng.add_questions(eid,r.questions)}
    @api.get("/api/exams/{eid}/questions")
    def lq(eid:str):return {"questions":eng.questions(eid)}
    @api.post("/api/exams/{eid}/diagnostic")
    def diag(eid:str,r:Answers):return eng.diagnostic(eid,r.answers)
    @api.post("/api/exams/{eid}/probability-map")
    def pm(eid:str,r:Mastery):return eng.probability_map(eid,r.mastery)
    @api.post("/api/exams/{eid}/training")
    def tr(eid:str,r:Mastery):return eng.training_set(eid,r.mastery,r.count)
    @api.post("/api/exams/{eid}/readiness")
    def ready(eid:str,r:Mastery):return eng.readiness(eid,r.mastery)
    @api.post("/api/exams/{eid}/collect-sources")
    def collect(eid:str,r:Collect):
        task=eng.browser_collection_task(eid,r.query)
        if r.exchange_root:
            p=emit_task(__import__("pathlib").Path(r.exchange_root),task["task_type"],task,["browser.read","llm.reasoning"])
            task["task_file"]=str(p)
        return task

    @api.post("/api/exams/{eid}/sessions")
    def session(eid:str,r:SessionCreate): return eng.create_session(eid,r.learner_id,r.mode,r.language)
    @api.post("/api/sessions/{sid}/answer")
    def answer(sid:str,r:AnswerSubmit):
        try:return eng.submit_answer(sid,r.question_id,r.answer)
        except KeyError as e:raise HTTPException(404,str(e))
    @api.get("/api/sessions/{sid}/report")
    def session_report(sid:str): return eng.session_report(sid)
    @api.post("/api/exams/{eid}/mock")
    def mock(eid:str,r:MockReq): return eng.mock_exam(eid,r.count)
    @api.get("/api/learners/{learner_id}/usage")
    def usage(learner_id:str): return eng.usage_minutes(learner_id)
    @api.get("/api/exams/{eid}/provenance")
    def provenance(eid:str): return eng.provenance_report(eid)

    @api.get("/api/audit")
    def audit(limit:int=100):return {"events":jobs.store.audit_list(limit)}
    return api
app=build_app()
