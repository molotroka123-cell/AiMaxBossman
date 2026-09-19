#!/usr/bin/env python3
"""Замер задержки ручек, которые видит владелец.

Не бенчмарк и не сравнение с прошлым релизом: одна цифра на маршрут, чтобы
«стало медленно» перестало быть ощущением. Запускается из каталога
`command-center`:

    python ../tools/measure_owner_paths.py

Меряется медиана трёх вызовов через ASGI, без сети и без сервера: интересует
время НАШЕГО кода, а не round-trip до localhost. Маршруты берутся из
роутеров фич, потому что приложение подключает их лениво, и `app.routes` до
первого запроса почти пуст.

Так были найдены две потери: `ffmpeg -filters/-encoders` на каждый запрос
`/api/video-studio/capabilities` и сон `cpu_percent(interval=0.1)` внутри
наблюдения хоста, дававший по 115 мс двум ручкам `/api/reality/*`.
"""
import asyncio, statistics, time, sys, tempfile
from pathlib import Path
sys.path.insert(0, '.')

async def main():
    import httpx
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings
    tmp = Path(tempfile.mkdtemp())
    t0 = time.perf_counter()
    app = create_app(Settings(data_dir=tmp/"data",
                              database_url=f"sqlite+aiosqlite:///{tmp/'data'/'m.db'}",
                              ui_dir=tmp/"no-ui"), announce_token=False, start_workers=False)
    build_ms = (time.perf_counter()-t0)*1000
    svc = app.state.svc
    t1 = time.perf_counter()
    await svc.start()
    start_ms = (time.perf_counter()-t1)*1000
    print(f"create_app: {build_ms:.0f} ms   svc.start: {start_ms:.0f} ms")

    paths = set()
    for f in svc.features:
        if f.router is None:
            continue
        for r in f.router.routes:
            p = getattr(r, "path", "")
            methods = getattr(r, "methods", None) or set()
            if p and "{" not in p and "GET" in methods:
                paths.add("/api" + p)

    results = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://m", headers={HEADER: svc.auth.token},
                                 timeout=120) as c:
        for path in sorted(paths):
            times, code = [], None
            for _ in range(3):
                s = time.perf_counter()
                try:
                    r = await c.get(path)
                    code = r.status_code
                except Exception as e:
                    code = type(e).__name__
                times.append((time.perf_counter()-s)*1000)
            results.append((statistics.median(times), max(times), path, code))
    await svc.stop()
    results.sort(reverse=True)
    print(f"{'median':>9} {'max':>9}  code  path")
    for med, mx, path, code in results[:20]:
        print(f"{med:9.1f} {mx:9.1f}  {str(code):>4}  {path}")
    ok = [r for r in results if r[3] == 200]
    print(f"\nGET routes measured: {len(results)}  (200 OK: {len(ok)})")
    for limit in (1000, 500, 200, 100):
        print(f"  slower than {limit:4d} ms: {sum(1 for r in results if r[0] > limit)}")

asyncio.run(main())
