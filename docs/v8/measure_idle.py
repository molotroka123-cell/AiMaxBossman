"""Reproduce phase-0 Linux process measurements; no provider calls or model loads."""
import asyncio
import json
import platform
import subprocess
import tempfile
import time
from pathlib import Path

started = time.perf_counter()
import bcc.app
IMPORT_MS = (time.perf_counter() - started) * 1000
import httpx
import psutil
from bcc.api import create_app
from bcc.auth import HEADER
from bcc.config import Settings

async def main():
    with tempfile.TemporaryDirectory(prefix='studio-baseline-') as directory:
        app = create_app(Settings(data_dir=Path(directory)), announce_token=False, start_workers=True)
        svc = app.state.svc
        begin = time.perf_counter()
        await svc.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={HEADER: svc.auth.token}) as client:
                request_start = time.perf_counter()
                response = await client.get('/api/images/models')
                response.raise_for_status()
                first_ms = (time.perf_counter() - request_start) * 1000
            startup_ms = (time.perf_counter() - begin) * 1000
            process = psutil.Process()
            rss_before = process.memory_info().rss
            cpu_before = sum(process.cpu_times()[:2])
            start_idle = time.perf_counter()
            await asyncio.sleep(60)
            elapsed = time.perf_counter() - start_idle
            result = dict(source_sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(), platform=platform.platform(), python=platform.python_version(), import_ms=IMPORT_MS, first_answer_ms=first_ms, start_to_answer_ms=startup_ms, http_status=response.status_code, measurement_transport='in-process ASGI (not TCP)', idle_seconds=elapsed, rss_before_mib=rss_before/2**20, rss_after_mib=process.memory_info().rss/2**20, cpu_one_core_percent=(sum(process.cpu_times()[:2])-cpu_before)/elapsed*100, images_tick=svc.feature_ticks.get('images'), process_count=1+len(process.children(recursive=True)))
            assert result['images_tick'] and result['images_tick']['at'] > 0
            print(json.dumps(result, indent=2), flush=True)
        finally:
            await svc.stop()

asyncio.run(main())
