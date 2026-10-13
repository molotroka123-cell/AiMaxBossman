"""No network, GPU or Telegram messages: owner prompt-review contract."""
import asyncio
from types import SimpleNamespace
import pytest
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.adapters import Models
from bcc.telegram_companion.config import CompanionError, Person, Settings
from bcc.telegram_companion.store import Store

OWNER=Person(11111,11111,'owner',None)
GUEST=Person(22222,22222,'guest',None)
def test_review_does_not_generate_and_keeps_two_choices(tmp_path):
 async def run():
  calls=[]
  async def refine(instructions,prompt):
   calls.append(prompt); return 'A red car beside a blue lake, slow camera movement.'
  store=Store(tmp_path/'store.db')
  try:
   app=Companion(Settings((OWNER,),image_enabled=True),store,None,None,SimpleNamespace(refine_media_prompt=refine))
   reply=await app.prepare_media(OWNER,'video красная машина у синего озера')
   assert calls==['красная машина у синего озера']
   assert 'подготовка' in str(reply) and app.image_job is None
   commands=[v for v in app.buttons.values()]
   assert len(commands)==2
   assert '/video 1 ' in repr(commands)
  finally: store.close()
 asyncio.run(run())

@pytest.mark.parametrize('person,enabled,busy,code',[(GUEST,True,False,'IMAGE_GEN_GUESTS_DISABLED'),(OWNER,False,False,'IMAGE_GEN_DISABLED'),(OWNER,True,True,'IMAGE_GEN_BUSY')])
def test_guards_run_before_model(tmp_path,person,enabled,busy,code):
 async def run():
  store=Store(tmp_path/'store.db')
  try:
   app=Companion(Settings((OWNER,GUEST),image_enabled=enabled),store,None,None,None)
   if busy: app.image_job={'id':1}
   with pytest.raises(CompanionError,match=code): await app.prepare_media(person,'image a cat')
  finally: store.close()
 asyncio.run(run())

def test_explicit_uncensored_route_no_fallback():
 async def run():
  model=object.__new__(Models)
  model.settings=Settings((OWNER,),local_model='qwen-main',fast_url='http://127.0.0.1:11434/v1',fast_model='qwen-uncensored')
  model.lock=asyncio.Lock(); calls=[]
  async def local(*args): calls.append(args); return 'a cat'
  model._local=local
  assert await model.refine_media_prompt('instructions','кот')=='a cat'
  assert len(calls)==1 and calls[0][1]=='qwen-uncensored'
  async def failed(*args): raise CompanionError('MODEL_UNAVAILABLE')
  model._local=failed
  with pytest.raises(CompanionError,match='MODEL_UNAVAILABLE'): await model.refine_media_prompt('instructions','кот')
 asyncio.run(run())

def test_three_reference_order_and_no_training(tmp_path):
 async def run():
  from unittest.mock import AsyncMock
  store=Store(tmp_path/'store')
  try:
   store.put('media_refs:'+OWNER.key,{'1':'face-id','2':'body-id'})
   telegram=SimpleNamespace(fetch_file=AsyncMock(return_value=b'\x89PNG\r\n\x1a\n'+b'x'*40),send=AsyncMock())
   core=SimpleNamespace(studio_model=AsyncMock(return_value={'available':True}),studio_reference=AsyncMock(side_effect=['target-run','face-run','body-run']))
   models=SimpleNamespace(refine_media_prompt=AsyncMock(return_value='make the jacket red'))
   app=Companion(Settings((OWNER,),image_enabled=True),store,telegram,core,models)
   app.generate=AsyncMock(return_value='sent')
   assert await app.edit_from_references(OWNER,'target-id','make the jacket red')=='sent'
   assert [call.args[0] for call in telegram.fetch_file.call_args_list]==['target-id','face-id','body-id']
   assert app.generate.call_args.kwargs['reference_runs']==['target-run','face-run','body-run']
   assert 'Image 1 is the target' in app.generate.call_args.args[1]
   store.put('delegation_locked',True)
   with pytest.raises(CompanionError,match='MEDIA_STOPPED'): await app.edit_from_references(OWNER,'target-id','change clothes')
   assert app.generate.await_count==1
  finally: store.close()
 asyncio.run(run())

def test_references_required_before_fetch(tmp_path):
 async def run():
  store=Store(tmp_path/'store')
  try:
   app=Companion(Settings((OWNER,),image_enabled=True),store,None,None,None)
   assert '/ref 1' in await app.edit_from_references(OWNER,'target-id','a red shirt')
  finally: store.close()
 asyncio.run(run())

def test_reference_mode_is_owner_scoped_and_survives_restart(tmp_path):
 async def run():
  store=Store(tmp_path/'store')
  settings=Settings((OWNER,GUEST),image_enabled=True)
  app=Companion(settings,store,None,None,None)
  def message(person,text): return {'from':{'id':person.user_id,'is_bot':False},'chat':{'id':person.chat_id,'type':'private'},'text':text}
  assert 'Сначала' in await app.handle(OWNER,message(OWNER,'/refs on'))
  store.put('media_refs:'+OWNER.key,{'1':{'run_id':'face'},'2':{'run_id':'body'}})
  assert 'включён' in await app.handle(OWNER,message(OWNER,'/refs on'))
  with pytest.raises(CompanionError): await app.handle(GUEST,message(GUEST,'/refs on'))
  store.close(); store=Store(tmp_path/'store')
  app=Companion(settings,store,None,None,None)
  try:
   assert store.get('media_refs_active:'+OWNER.key) is True
   assert await app.saved_reference_runs(OWNER)==['face','body']
   await app.handle(OWNER,message(OWNER,'/refs off'))
   assert store.get('media_refs_active:'+OWNER.key) is False
   assert store.get('media_refs:'+OWNER.key)['1']['run_id']=='face'
   await app.handle(OWNER,message(OWNER,'/refs clear'))
   assert store.get('media_refs:'+OWNER.key)=={}
  finally: store.close()
 asyncio.run(run())
