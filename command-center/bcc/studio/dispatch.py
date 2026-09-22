"""Lazy providers with one durable admission record and no automatic resubmission."""
import asyncio
import base64
import os
import sqlalchemy as sa
from bcc.studio.provider import GenerationPlane,ProviderFailure
from bcc.studio.runtime import StudioError,storage,persist,one,verified_handle,provider_fingerprint
from bcc.studio.tables import jobs,runs,config
from bcc.studio import governance as gov
from bcc.v2.images_tables import image_jobs
from sqlalchemy.dialects.sqlite import insert

async def inputs_for(svc,inputs):
    result=[]
    for item in inputs:
        row=await one(svc,runs,runs.c.id,item['run_id'])
        if not row or row['deleted'] or row['sha256']!=item['sha256']:raise StudioError('egress','Reference changed or deleted','OWNER_REQUIRED')
        if row['surface']!='image' or row['mime']=='image/svg+xml':raise StudioError('egress','Only raster image references are supported','OWNER_REQUIRED')
        if row['file_bytes']>15*1024*1024:raise ValueError('reference exceeds 15 MiB')
        handle=await verified_handle(svc,row)
        # Позиционное чтение, а не последовательное: проверка digest_descriptor
        # на Windows оставляет общий указатель дескриптора в конце файла, и
        # os.read вернул бы ноль байт — референс объявлялся бы изменившимся,
        # хотя не менялся. Тот же корень, что у рефрейма (BL-081).
        def read_all():
            from bcc.video_studio.media import pread
            parts=[];offset=0;limit=row['file_bytes']+1
            while offset<limit:
                block=pread(handle.descriptor,min(256*1024,limit-offset),offset)
                if not block:break
                parts.append(block);offset+=len(block)
            return b''.join(parts)
        try:data=await asyncio.to_thread(read_all)
        finally:handle.close()
        import hashlib
        if hashlib.sha256(data).hexdigest()!=item['sha256']:raise StudioError('egress','Reference changed during read','OWNER_REQUIRED')
        result.append({**item,'data_uri':f"data:{row['mime']};base64,"+base64.b64encode(data).decode()})
    return tuple(result)

async def salvage_partial(svc,job,plane,model,provider,request_id,reason):
    """«Стоп обрывает на том, что уже есть»: сохранить сегменты, которые движок реально доделал.

    Возвращает run_id или None. None означает «сохранять нечего» — ноль готовых сегментов,
    провайдер без такой возможности, или байты не прошли верификацию. Пустой или битый файл
    не выдаётся за результат никогда, и задание от этого не становится завершённым.
    """
    if getattr(provider,'partial_result',None) is None:return None
    try:info=provider.partial_result(request_id)
    except (ValueError,KeyError):return None
    if not info or not info.get('segments_done'):return None
    path=storage(svc).root/f"generated-{job['id']}-partial.mp4"
    try:result=await provider.fetch_partial(request_id,path)
    except asyncio.CancelledError:raise
    except Exception:
        try:path.unlink(missing_ok=True)
        except OSError:pass
        return None
    detail={**info,'stopped_by':reason}
    effective={**plane['settings'],'engine_trace':provider.traces.get(request_id)}
    try:rid=await persist(svc,job['id'],plane,model,result.path,request_id=request_id,cost=0,effective_settings=effective,partial=detail)
    except asyncio.CancelledError:raise
    except Exception:rid=None            # верификация не прошла -> не сохраняем ничего
    if rid is None:
        try:result.path.unlink(missing_ok=True)
        except OSError:pass
        return None
    await svc.bus.emit('studio.job.partial',job_id=job['id'],run_id=rid,reason=reason,
                       segments_done=info['segments_done'],segments_total=info['segments_total'])
    return rid

async def generate(svc,job,ext,model):
    plane=ext['plane'];reservation=None
    if model['provider']=='comfyui':
        from bcc.oss.comfyui import image_configuration,ComfyUIImageProvider
        from bcc.studio.providers.comfyui import ComfyUIProvider
        settings=image_configuration()
        if settings is None:raise StudioError('unauthorized','OWNER_REQUIRED: configure local ComfyUI checkpoint','OWNER_REQUIRED')
        provider=ComfyUIProvider(ComfyUIImageProvider(*settings),storage(svc).root)
        media=()
    elif model['provider']=='sdcpp':
        from bcc.studio.providers.sdcpp import SdCppProvider,configuration
        cfg=configuration()
        if cfg is None:raise StudioError('unauthorized','OWNER_REQUIRED: configure BOSSMAN_SDCPP_BIN and BOSSMAN_MEDIA_MODELS','OWNER_REQUIRED')
        provider=SdCppProvider(cfg,storage(svc).root,model)
        media=await inputs_for(svc,plane['media'])
    elif model['provider']=='openrouter':
        from bcc.v2.openrouter_identity import resolve
        from bcc.studio.providers.openrouter import OpenRouterProvider
        credential=await resolve(svc.db,svc.vault)
        if not credential.configured:raise StudioError('unauthorized','OWNER_REQUIRED: OpenRouter key missing','OWNER_REQUIRED')
        if credential.conflicts:raise StudioError('unauthorized','OWNER_REQUIRED: conflicting OpenRouter credential sources','OWNER_REQUIRED')
        reservation=await gov.reserve(svc,model['id'],job['id'],plane['count'],plane['media'])
        async def gate():
            await gov.check_current(svc,reservation)
            current=await one(svc,image_jobs,image_jobs.c.id,job['id'])
            if current['status']!='running':raise StudioError('canceled','Job is no longer admitted')
            cost=reservation['policy']['prices'][model['id']]
            return {'kind':'cloud','pricing_known':True,'price_in':cost,'price_out':cost}
        provider=OpenRouterProvider(credential.key,allowed_download_hosts=reservation['policy']['download_hosts'],gate=gate)
        remaining=await provider.balance()
        if remaining is not None and remaining<reservation['upper_bound_usd']:raise StudioError('insufficient_credit','OWNER_REQUIRED: balance below reserved upper bound','OWNER_REQUIRED')
        media=await inputs_for(svc,plane['media'])
    else:
        raise StudioError('unauthorized','OWNER_REQUIRED: official MCP acceptance not supplied','OWNER_REQUIRED')
    try:
        for index in range(plane['count']):
            async with svc.db.session() as s:
                await s.execute(sa.update(jobs).where(jobs.c.job_id==job['id']).values(submit_started=True));await s.commit()
            settings=dict(plane['settings'])
            if settings.get('seed') is not None:settings['seed']=(settings['seed']+index)%(2**63)
            receipt=await provider.submit(GenerationPlane(model['id'],plane['prompt'],settings,media))
            async with svc.db.session() as s:
                await s.execute(sa.update(jobs).where(jobs.c.job_id==job['id']).values(request_id=receipt.request_id));await s.commit()
            while True:
                status=await provider.status(receipt.request_id)
                if status.reason:
                    if status.reason=='timeout':await salvage_partial(svc,job,plane,model,provider,receipt.request_id,'timeout')
                    raise ProviderFailure(status)
                if status.state=='completed':break
                if status.state=='canceled':
                    await salvage_partial(svc,job,plane,model,provider,receipt.request_id,'canceled')
                    raise StudioError('canceled','Provider canceled')
                await asyncio.sleep(0.5)
            for n,output in enumerate(status.outputs):
                suffix='.png' if model['surface']=='image' else '.mp4'
                path=storage(svc).root/f"generated-{job['id']}-{index}-{n}{suffix}"
                result=await provider.fetch(output,path)
                cost=0 if model['provider'] in ('comfyui','sdcpp') else provider.costs.get(receipt.request_id,'NOT_CAPTURED:provider_did_not_report')
                external_id=provider.external_ids[receipt.request_id] if model['provider']=='openrouter' else receipt.request_id
                # Local engine: the execution trace (argv, model hashes, time, peak memory,
                # raw engine output hash) is part of immutable provenance — proof of generation.
                effective={**settings,'engine_trace':provider.traces.get(receipt.request_id)} if model['provider']=='sdcpp' else settings
                try:rid=await persist(svc,job['id'],plane,model,result.path,request_id=external_id,cost=cost,effective_settings=effective)
                except BaseException:result.path.unlink(missing_ok=True);raise
                if rid is None:result.path.unlink(missing_ok=True);return
                if type(cost) in (int,float) and reservation and cost>reservation['policy']['prices'][model['id']]:
                    # Provider exceeded the owner's estimate: retain bytes but stop further calls.
                    p=await gov.policy(svc);p['enabled']=False;await gov.save_policy(svc,p)
                    raise StudioError('budget','Provider charge exceeded reserved upper bound; disabled','OWNER_REQUIRED')
                live=(provider.provider.client.transport is None) if model['provider']=='comfyui' else (not getattr(provider,'fake',False)) if model['provider']=='sdcpp' else provider._transport is None
                if live:
                    async with svc.db.session() as s:
                        proof={'configuration':await provider_fingerprint(svc,model['id']),'run_id':rid,'model':model['id'],'at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
                        await s.execute(insert(config).values(key='probe:'+model['id'],value=proof).on_conflict_do_update(index_elements=['key'],set_={'value':proof}));await s.commit()
    except asyncio.CancelledError:
        if 'receipt' in locals():
            await provider.cancel(receipt.request_id)
            # Отмена владельцем: сначала сохранить готовую часть, потом уже уходить.
            await salvage_partial(svc,job,ext['plane'],model,provider,receipt.request_id,'canceled')
        raise
