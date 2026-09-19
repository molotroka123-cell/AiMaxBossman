"""Local Studio wrapper over the existing native ComfyUI implementation.

No worker/poller is started here. Cancellation abandons this wrapper's job;
ComfyUI has no per-prompt interruption in the existing client, so it may still
compute remotely. Never issue a global interrupt that stops somebody else's job.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import httpx
from bcc.oss.comfyui import ComfyUIImageProvider, verify_png
from bcc.v2.images_runtime import ImageStorage
from bcc.studio.catalog import load, validate_settings
from bcc.studio.provider import GenerationPlane, Submitted, ProviderOutput, ProviderStatus, Fetched, ProviderFailure, classify_response

class ComfyUIProvider:
    name = 'comfyui'

    def __init__(self, provider: ComfyUIImageProvider, storage_root: Path):
        self.provider = provider
        self.storage = ImageStorage(storage_root)
        self._jobs: dict[str, dict] = {}
        self._outputs: dict[str, tuple[str, dict]] = {}

    async def submit(self, plane: GenerationPlane) -> Submitted:
        if plane.model != 'comfyui:*': raise ValueError('model: select configured comfyui:*')
        if plane.media: raise ValueError('media: text-to-image only')
        if not isinstance(plane.prompt,str) or not plane.prompt.strip(): raise ValueError('prompt: required')
        model = next(m for m in load()['models'] if m['id'] == plane.model)
        settings = validate_settings(model,plane.settings)
        workflow = self.provider.workflow({**settings,'prompt':plane.prompt},0)
        try:
            request_id = await self.provider.client.submit(workflow)
        except httpx.HTTPStatusError as exc:
            raise ProviderFailure(classify_response(exc.response.status_code,None)) from None
        except (httpx.TimeoutException,TimeoutError):
            raise ProviderFailure(ProviderStatus('timeout','timeout')) from None
        except httpx.TransportError:
            raise ProviderFailure(ProviderStatus('failed','provider_down')) from None
        except (ValueError,TypeError):
            raise ProviderFailure(ProviderStatus('failed','malformed')) from None
        self._jobs[request_id] = {'settings':settings,'canceled':False}
        return Submitted(request_id)  # no remote cancellation capability claimed

    def _job(self, request_id):
        if request_id not in self._jobs: raise ValueError('request_id: unknown to this adapter')
        return self._jobs[request_id]

    async def status(self, request_id):
        job = self._job(request_id)
        if job['canceled']: return ProviderStatus('canceled')
        try:
            status = await self.provider.client.status(request_id)
        except httpx.HTTPStatusError as exc:
            return classify_response(exc.response.status_code,None)
        except (httpx.TimeoutException,TimeoutError):
            return ProviderStatus('timeout','timeout')
        except httpx.TransportError:
            return ProviderStatus('failed','provider_down')
        except (ValueError,TypeError):
            return ProviderStatus('failed','malformed')
        if job['canceled']: return ProviderStatus('canceled')
        if status['state'] == 'pending': return ProviderStatus('running')
        if status['state'] == 'failed': return ProviderStatus('failed','provider_down')
        if status['state'] != 'completed' or len(status['outputs']) != 1:
            return ProviderStatus('failed','malformed')
        ref = f'{request_id}:0'
        self._outputs[ref] = (request_id,dict(status['outputs'][0]))
        return ProviderStatus('completed',outputs=(ProviderOutput(ref),))

    async def cancel(self, request_id):
        self._job(request_id)['canceled'] = True

    async def fetch(self, output: ProviderOutput, dest: Path):
        if output.ref not in self._outputs: raise ValueError('output: unknown')
        request_id, descriptor = self._outputs[output.ref]
        job = self._job(request_id)
        if job['canceled']: raise ValueError('output: job canceled')
        dest = Path(dest).resolve()
        try: relative = str(dest.relative_to(self.storage.root))
        except ValueError as exc: raise PermissionError('output path escapes media root') from exc
        data, mime, _ = await self.provider.client.download(descriptor)
        width,height = verify_png(data)
        if (width,height) != (job['settings']['width'],job['settings']['height']):
            raise ValueError('output dimensions differ from requested width/height')
        if mime != 'image/png': raise ValueError('output mime mismatch')
        if job['canceled']: raise ValueError('output: job canceled during fetch')
        path = self.storage.save(relative,data)
        return Fetched(path,len(data),mime,hashlib.sha256(data).hexdigest(),width,height)
