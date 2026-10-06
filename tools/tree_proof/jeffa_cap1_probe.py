"""cap-1 'Память участника и согласие' (docs-sourced capability): proxy receipt.
Import bcc.pit.vault + bcc.pit.passport_commands (clean subprocess, this checkout first) and run the existing
tests that exercise /memory /pause_memory /forget and the consent/privacy rules. Writes evidence/out/cap-1.txt
and evidence/jeffa-cap1.json (merged into jeffa.json by jeffa_build.py)."""
import hashlib, importlib.util, json, os, re, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sp = importlib.util.spec_from_file_location("jeffa_probe", HERE / "jeffa_probe.py")
jp = importlib.util.module_from_spec(sp); sp.loader.exec_module(jp)
op, ROOT, EVID = jp.op, jp.ROOT, jp.EVID
TESTS = ["tests/test_jeff_privacy_in_code.py", "tests/test_jeff_memory_self_description.py",
         "tests/test_jeff_2_memory_palace.py", "tests/test_pit_passport_commands.py"]
TESTS = [t for t in TESTS if (ROOT / "command-center" / t).is_file()]
sha, started = op.sha_head(), op.now()
tmp = tempfile.mkdtemp(prefix="cap1_"); env = op.clean_env(tmp)
root = str(ROOT).replace("\\", "/")
code = ("import sys,importlib;fs=[]\nfor m in ('bcc.pit.vault','bcc.pit.passport_commands'):\n"
        " x=importlib.import_module(m);f=x.__file__.replace(chr(92),'/');print('IMPORTED',m,f);fs.append(f)\n"
        "sys.exit(0 if all(f.lower().startswith(%r.lower()) for f in fs) else 3)") % root
rc, out1, dt1 = op.run([op.PY, "-c", code], str(ROOT), env, 60)
log = [f"$ import bcc.pit.vault bcc.pit.passport_commands\n{out1}\n[exit {rc} in {dt1}s]"]
cmd = f"(cd command-center && python -m pytest -q --timeout=60 -p no:cacheprovider {' '.join(TESTS)})"
rc2, out2, dt2 = op.run([op.PY, "-m", "pytest", "-q", "--timeout=60", "-p", "no:cacheprovider", *TESTS],
                        str(ROOT / "command-center"), env, 300)
log.append(f"$ {cmd}\n{out2}\n[exit {rc2} in {dt2}s]")
m = re.search(r"(\d+) passed", out2)
ok = rc == 0 and rc2 == 0 and m and int(m.group(1)) >= 1
text = (f"node_id: cap-1\nprobe: capability proxy (vault+passport_commands import, consent/forget tests)\nsha: {sha}\n\n"
        + "\n".join(log) + f"\nVERDICT: {'PASS' if ok else 'FAIL'}\n")
(EVID / "out").mkdir(exist_ok=True)
data = text.encode("utf-8"); (EVID / "out" / "cap-1.txt").write_bytes(data)
rec = {"node_id": "cap-1", "sha": sha, "probe": "import+pytest (capability proxy: vault, passport_commands)",
       "command": f"python -c import bcc.pit.vault,bcc.pit.passport_commands && {cmd}",
       "exit_code": 0 if ok else (rc2 or rc), "started_at": started, "finished_at": op.now(),
       "output_sha256": hashlib.sha256(data).hexdigest(), "output_tail": text[-1500:],
       "verdict": "PASS" if ok else "FAIL", "kind": "pytest", "reason": "" if ok else "tests_failed"}
(EVID / "raw" / "jeffa-cap1.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
print(rec["verdict"], out2.strip().splitlines()[-1] if out2.strip() else "")
