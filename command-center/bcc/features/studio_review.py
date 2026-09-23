"""Bossman Vision review of Studio videos and the owner's feedback that teaches it."""
import asyncio
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from bcc.features import Feature
from bcc.studio import review as rv

router=APIRouter(prefix='/studio')
class Feedback(BaseModel):
    model_config=ConfigDict(extra='forbid')
    verdict:str=Field(pattern='^(good|bad)$')
    reason:str=Field(default='',max_length=500)

async def guard(coro):
    try:return await coro
    except KeyError:raise HTTPException(404,'Studio run not found') from None
    except ValueError as e:raise HTTPException(422,str(e)) from None

@router.get('/runs/{rid}/review')
async def get_review(rid:str,request:Request):return await guard(rv.get(request.app.state.svc,rid))
@router.post('/runs/{rid}/review')
async def run_review(rid:str,request:Request):
    svc=request.app.state.svc
    await guard(rv.review_run(svc,rid))
    return await rv.get(svc,rid)
@router.post('/runs/{rid}/feedback')
async def feedback(rid:str,body:Feedback,request:Request):
    svc=request.app.state.svc
    await guard(rv.record_feedback(svc,rid,body.verdict,body.reason))
    return await rv.get(svc,rid)
@router.get('/review/stats')
async def stats(request:Request):return await rv.stats(request.app.state.svc)

async def setup(svc):
    # A background loop like the worker: not in the worker-less app the tests build.
    if not getattr(svc,'start_workers',False):return
    task=asyncio.create_task(rv.watch(svc),name='bcc-studio-vision-review')
    if hasattr(svc,'_tasks'):svc._tasks.append(task)

FEATURE=Feature(name='studio_review',router=router,setup=setup)
