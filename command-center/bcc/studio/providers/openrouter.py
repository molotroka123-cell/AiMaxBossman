"""OpenRouter image/video shapes already used by repository scripts.

HTTP is bounded; no implicit retry, no token on CDN, no guessed audio API.
An injectable transport exists for contract tests only, never owner evidence.
"""
import base64
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import httpx
from bcc.studio.provider import Submitted,ProviderOutput,ProviderStatus,Fetched,ProviderFailure,classify_response

BASE='https://openrouter.ai/api/v1'
LIMIT=256*1024*1024

class OpenRouterProvider:
    name='openrouter'
    def __init__(self,key,*,transport=None,allowed_download_hosts=(),gate=None):
        self._key=key;self._transport=transport;self._hosts=set(allowed_download_hosts);self._gate=gate
        self._jobs={};self._outputs={};self.costs={}

    async def _request(self,method,path,payload=None):
        if self._gate:pricing=await self._gate()
        elif self._transport is not None:pricing={'kind':'cloud','pricing_known':True,'price_in':0,'price_out':0}  # isolated test transport
        else:raise ProviderFailure(ProviderStatus('failed','unauthorized'))
        from bcc.provider_governance import GovernedAdapter
        owner=self
        class Operation:
            async def chat(self):return await owner._http_request(method,path,payload)
        # The persisted reservation supplies an owner-approved per-generation
        # upper bound. GovernedAdapter additionally enforces execution privacy.
        guarded=GovernedAdapter(Operation(),{'kind':'openai_compat','base_url':BASE},
            pricing)
        return await guarded.chat()

    async def _http_request(self,method,path,payload=None):
        if not self._key:raise ProviderFailure(ProviderStatus('failed','unauthorized'))
        if self._gate:await self._gate()
        try:
            async with httpx.AsyncClient(transport=self._transport,timeout=60,follow_redirects=False,trust_env=False) as c:
                async with c.stream(method,BASE+path,json=payload,headers={'Authorization':'Bearer '+self._key}) as r:
                    if not 200<=r.status_code<300:raise ProviderFailure(classify_response(r.status_code,None))
                    raw=bytearray()
                    async for chunk in r.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw)>96*1024*1024:raise ValueError('response too large')
                    body=json.loads(raw)
                    if not isinstance(body,dict):raise ValueError('response not object')
                    error=body.get('error')
                    if error:
                        code=error.get('code') if isinstance(error,dict) else None
                        reason=code if code in ('insufficient_credit','content_policy') else 'malformed'
                        raise ProviderFailure(ProviderStatus('refused' if reason=='content_policy' else 'failed',reason))
                    return body
        except ProviderFailure:raise
        except httpx.TimeoutException:raise ProviderFailure(ProviderStatus('timeout','timeout')) from None
        except httpx.TransportError:raise ProviderFailure(ProviderStatus('failed','provider_down')) from None
        except (ValueError,TypeError):raise ProviderFailure(ProviderStatus('failed','malformed')) from None

    async def balance(self):
        data=(await self._request('GET','/auth/key')).get('data',{})
        remaining=data.get('limit_remaining')
        if type(remaining) not in (int,float):return None
        if remaining<=0:raise ProviderFailure(ProviderStatus('failed','insufficient_credit'))
        return remaining

    async def submit(self,plane):
        model=plane.model.removeprefix('openrouter:')
        video=model in ('minimax/hailuo-3-max','bytedance/seedance-2.0-mini')
        payload={'model':model,'prompt':plane.prompt,**plane.settings}
        if plane.media:
            refs=[];frames=[]
            for item in plane.media:
                uri=item['data_uri']
                if item['role'] in ('start','end'):
                    frames.append({'type':'image_url','image_url':{'url':uri},'frame_type':'first_frame' if item['role']=='start' else 'last_frame'})
                else:refs.append({'type':'image_url','image_url':{'url':uri}})
            if refs:payload['input_references']=refs
            if frames:payload['frame_images']=frames
        body=await self._request('POST','/videos' if video else '/images',payload)
        rid=body.get('id') if video else uuid4().hex
        if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',rid):raise ProviderFailure(ProviderStatus('failed','malformed'))
        self._jobs[rid]={'video':video,'body':body,'canceled':False}
        self._cost(rid,body)
        return Submitted(rid)

    def _cost(self,rid,body):
        import math
        value=(body.get('usage') or {}).get('cost')
        if type(value) in (int,float) and math.isfinite(value) and value>=0:self.costs[rid]=value

    async def status(self,rid):
        if rid not in self._jobs:raise ValueError('request_id: unknown')
        job=self._jobs[rid]
        if job['canceled']:return ProviderStatus('canceled')
        body=await self._request('GET','/videos/'+rid) if job['video'] else job['body']
        self._cost(rid,body)
        if job['video']:
            state=body.get('status')
            if state in ('pending','queued','in_progress','running'):return ProviderStatus('running')
            if state!='completed':return classify_response(200,{'status':state,'error':body.get('error')})
            values=body.get('unsigned_urls')
        else:values=body.get('data')
        if not isinstance(values,list) or not values:raise ProviderFailure(ProviderStatus('failed','malformed'))
        outputs=[]
        for i,value in enumerate(values):
            ref=f'{rid}:{i}'
            if job['video']:
                if not isinstance(value,str):raise ProviderFailure(ProviderStatus('failed','malformed'))
                self._outputs[ref]=('url',value)
            else:
                if not isinstance(value,dict) or not isinstance(value.get('b64_json'),str):raise ProviderFailure(ProviderStatus('failed','malformed'))
                self._outputs[ref]=('base64',value['b64_json'])
            outputs.append(ProviderOutput(ref))
        return ProviderStatus('completed',outputs=tuple(outputs))

    async def cancel(self,rid):
        if rid not in self._jobs:raise ValueError('request_id: unknown')
        self._jobs[rid]['canceled']=True  # local stop only; no invented cancel endpoint

    async def fetch(self,output,dest):
        if output.ref not in self._outputs:raise ValueError('output: unknown')
        kind,value=self._outputs[output.ref]
        try:
            if kind=='base64':data=base64.b64decode(value,validate=True)
            else:
                parsed=urlsplit(value)
                if parsed.scheme!='https' or parsed.hostname not in self._hosts or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('CDN not allowed')
                try:
                    if not ipaddress.ip_address(parsed.hostname).is_global:raise ValueError('private CDN')
                except ValueError as exc:
                    if str(exc)=='private CDN':raise
                if self._gate:await self._gate()
                if self._transport is None:
                    from bcc.plugin_security import safe_get
                    response=await safe_get(value,allowed_hosts=self._hosts,max_bytes=LIMIT,timeout=120,max_redirects=0)
                    if response.status_code!=200:raise ValueError('CDN status')
                    data=response.content
                else:
                    async with httpx.AsyncClient(transport=self._transport,follow_redirects=False,timeout=120) as c:
                        response=await c.get(value)  # deliberately NO auth headers
                        if response.status_code!=200:raise ValueError('CDN status')
                        data=response.content
            if not data or len(data)>LIMIT:raise ValueError('output size')
        except Exception as exc:
            from bcc.studio.runtime import StudioError
            if isinstance(exc,(ProviderFailure,StudioError)):raise
            raise ProviderFailure(ProviderStatus('failed','malformed')) from None
        dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(data)
        return Fetched(dest,len(data),'video/mp4' if kind=='url' else 'image/png',hashlib.sha256(data).hexdigest())
