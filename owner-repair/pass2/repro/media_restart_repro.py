"""MEDIA-RESTART repro: a backend that spawns the engine exactly like SdCppProvider._run is hard-killed;
does the engine child survive (orphan holding GPU/RAM, still writing into engine-work)?"""
import asyncio, subprocess, sys, time, os, psutil
if sys.argv[1:] == ["backend"]:
    sys.path.insert(0, r"C:\Users\asd\Bossman\wt-release\command-center")
    from bcc.video_studio.media import child_priority_kwargs
    async def main():
        p = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            **child_priority_kwargs())
        print(p.pid, flush=True); await asyncio.sleep(120)
    asyncio.run(main())
else:
    b = subprocess.Popen([sys.executable, __file__, "backend"], stdout=subprocess.PIPE, text=True)
    child = int(b.stdout.readline()); time.sleep(1)
    b.kill(); b.wait()                        # hard kill = crash / power-user taskkill /F
    time.sleep(2)
    alive = psutil.pid_exists(child) and psutil.Process(child).status() != "zombie"
    print({"backend_killed": True, "engine_child_pid": child, "engine_child_alive_after_backend_death": alive})
    if alive: psutil.Process(child).kill()
