"""Install the seed tools into the operator's custom-tool store (same layout as tool.create: <root>/<name>/tool.json
+ main.py, versioned, sha256). Refuses any code the operator's own tool.create would refuse.

    python tools/pc_operator_seed_tools/install.py [--check]
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "command-center"))
sys.path.insert(0, str(HERE))

from bcc.features import tools_selfext as sx  # noqa: E402
from bcc.features.tools_terminal import extra_ask_reason  # noqa: E402
import pc_tools  # noqa: E402

MARK = 'if __name__ == "__main__":  # install.py inserts _TOOL_NAME above this line'


def main() -> int:
    src = (HERE / "pc_tools.py").read_text(encoding="utf-8")
    assert MARK in src
    check_only = "--check" in sys.argv
    for name, (_fn, desc, args) in pc_tools.TOOLS.items():
        code = src.replace(MARK, f"_TOOL_NAME = {name!r}\n\n\n" + MARK)
        refused = sx.code_refusal(code)
        ask = extra_ask_reason(code)
        if refused or ask:
            print(f"{name}: REFUSED {refused or ask}")
            return 1
        if check_only:
            continue
        d = sx._tool_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        mf = d / "tool.json"
        old = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else None
        sha = hashlib.sha256(code.encode("utf-8")).hexdigest()
        if old and old.get("sha256") == sha and old.get("description") == desc:
            print(f"{name}: unchanged v{old['version']}")
            continue
        version = int((old or {}).get("version") or 0) + 1
        if old and (d / "main.py").is_file():
            (d / "main.py").replace(d / f"v{version - 1}.py")
        (d / "main.py").write_text(code, encoding="utf-8", newline="\n")
        schema = {k: {"type": "string", "description": v} for k, v in args.items()}
        mf.write_text(json.dumps({"name": name, "description": desc, "language": "python", "args_schema": schema,
                                  "version": version, "sha256": sha, "agent_id": 82, "task_id": None,
                                  "seeded_by": "claude pc-bench 12.11", "updated_at": int(time.time())},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{name}: v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
