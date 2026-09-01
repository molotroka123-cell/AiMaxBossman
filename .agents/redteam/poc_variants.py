"""Variant re-attacks after fix + no-false-negative check."""
import asyncio, os, subprocess, sys, tempfile, pathlib
sys.path.insert(0, r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core")
os.environ.setdefault("SANDBOX_MODE","docker")
from bossman.toolkit import ToolContext, files

root = pathlib.Path(tempfile.mkdtemp(prefix="bossman_var_"))
outside = root/"outside"; outside.mkdir(); (outside/"s.txt").write_text("BOSSMAN_TEST_SECRET_9F31A7\n")
ws = root/"workspace"/"coder"; ws.mkdir(parents=True)
(ws/"ok.txt").write_text("hello 9F31A7 inside\n")   # legit in-workdir hit
ctx = ToolContext(agent="coder", run_id=1, workdir=ws)

# Variant 1: junction inside workdir, default glob
jdir = ws/"j"
subprocess.run(["cmd","/c","mklink","/J",str(jdir),str(outside)],capture_output=True)
r = asyncio.run(files.fs_search({"pattern":"9F31A7"}, ctx))
print("V1 junction+default-glob:", "LEAK" if "s.txt" in r.content else "blocked", "| legit-hit:", "ok.txt" in r.content)

# Variant 2: junction + explicit glob into it
r = asyncio.run(files.fs_search({"pattern":"9F31A7","glob":"j/*"}, ctx))
print("V2 junction+glob j/*:", "LEAK" if "s.txt" in r.content or ("9F31A7" in r.content and "ok.txt" not in r.content) else "blocked")

# Variant 3: nested traversal ../coder/../coder-secrets style read
sib = root/"workspace"/"coder-secrets"; sib.mkdir(); (sib/"x").write_text("BOSSMAN_TEST_SECRET_9F31A7")
try:
    r = asyncio.run(files.fs_read({"path":"../coder/../coder-secrets/x"}, ctx))
    print("V3 nested sibling read:", "LEAK" if "9F31A7" in r.content else "blocked")
except PermissionError:
    print("V3 nested sibling read: blocked")

# Variant 4: fs.list into junction dir must not enumerate outside
r = asyncio.run(files.fs_list({"path":".","depth":3}, ctx))
print("V4 fs.list junction recurse:", "LEAK" if "s.txt" in r.content else "blocked (j listed as entry, not recursed)")

# No-false-negative: legit search returns the in-workdir hit
r = asyncio.run(files.fs_search({"pattern":"inside","glob":"*.txt"}, ctx))
print("V5 legit in-workdir search works:", "ok.txt" in r.content)
