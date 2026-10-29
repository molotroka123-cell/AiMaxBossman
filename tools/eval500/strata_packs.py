"""Strata pack bake-off: every Qwen3.8-Flash-Next pack Strata offers for Strix Halo, one at a time on :8090.

    python tools/eval500/strata_packs.py run      # all packs: load, speed 4K/32K, 14-scenario score (1 h budget), fast config
    python tools/eval500/strata_packs.py judge --loop
    python tools/eval500/strata_packs.py report --out docs/audits/2026-10-25-strata-packs

Reuses tournament.py unchanged: the SAME scenario ids, per-scenario timeout, clone of agent 41, blind judge and
scoring as recorded in docs/audits/2026-10-25-tournament/receipt.json. Each pack is its own arm with its own Bossman
model entry (provider 16, price 0, not default, not assigned to any agent). Only one model is in memory at a time:
Ollama models are unloaded first, Strata is stopped after each pack. Server binds 127.0.0.1 only (--access local).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("EVAL500_TOURNAMENT_ID", "strata-packs-20261025")
from eval500 import runner, schema, tournament as T  # noqa: E402

import psutil  # noqa: E402

REPO = HERE.parent.parent
T_RECEIPT = REPO / "docs" / "audits" / "2026-10-25-tournament" / "receipt.json"
DATA = r"D:\Bossman\models\llm\strata\data"
PORT = 8090
URL = f"http://127.0.0.1:{PORT}"
PROVIDER = 16
SCORING_BUDGET_S = 3600
FAST_SET = {"STRATA_PF_FUSED": "1", "STRATA_PF_GEMM": "1", "STRATA_HC_UPMIX": "1", "STRATA_PA_FAST": "1",
            "STRATA_HIP_WMMA": "1", "STRATA_SELECT_WMMA": "1", "STRATA_PF_SWITCH_MIN_T": "4096",
            "STRATA_PREFILL_STREAM_MIN": "128"}
# priority order for the 1-hour scoring budget: IQ3_S is the tournament reference, the 4-bit packs are the quality
# candidates, IQ3_XXS (smallest) last
PACKS = {
    "iq3_s": {"model": "iq3_s", "family": "qwen", "bits": "3.5 bpw avg (ISTA-DASLab GSQ-RCO IQ3_S)",
              "repo": "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF", "model_id": 107},
    "ud_iq4_xs": {"model": "ud-iq4_xs", "family": "unsloth", "bits": "~4 bpw (Unsloth Dynamic UD-IQ4_XS)",
                  "repo": "unsloth/Qwen3.8-Flash-Next-GGUF"},
    "ud_q4_k_xl": {"model": "ud-q4_k_xl", "family": "unsloth", "bits": "~4.5 bpw (Unsloth Dynamic UD-Q4_K_XL, K-quant)",
                   "repo": "unsloth/Qwen3.8-Flash-Next-GGUF"},
    "iq3_xxs": {"model": "iq3_xxs", "family": "qwen", "bits": "3.0 bpw avg (ISTA-DASLab GSQ-RCO IQ3_XXS)",
                "repo": "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF"},
}
OUT_RUN = T.ROOT_DIR          # D:\Bossman\eval\runs\strata-packs-20261025
STATE = OUT_RUN / "packs-state.json"


def log(m: str) -> None:
    print(f"[packs {time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_state() -> dict:
    return T._load(STATE, {"packs": {}})


def save_state(st: dict) -> None:
    T._save(STATE, st)


# ---------------------------------------------------------------- the tournament's exact sample

def tournament_receipt() -> dict:
    return json.loads(T_RECEIPT.read_text(encoding="utf-8"))


def fixed_sample() -> list[dict]:
    ids = tournament_receipt()["sample"]["ids"]
    dev = {s["id"]: s for s in schema.load_dev()}
    return [dev[i] for i in ids]


T.sample = fixed_sample      # every tournament function (run_arm, judge_all, build_receipt) now uses the same ids


# ---------------------------------------------------------------- RAM sampler

class RamPeak:
    def __init__(self) -> None:
        self.peak_sys = 0.0
        self.peak_strata = 0.0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def strata_gb() -> float:
        tot = 0
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                n = (p.info["name"] or "").lower()
                cl = " ".join(p.info["cmdline"] or []).lower()
                if "strata" in n or ("strata" in cl and "strata_packs" not in cl):
                    mi = p.memory_info()
                    tot += getattr(mi, "private", mi.rss)
            except (psutil.Error, OSError):
                pass
        return tot / 2**30

    def _run(self) -> None:
        while not self._stop.is_set():
            vm = psutil.virtual_memory()
            self.peak_sys = max(self.peak_sys, (vm.total - vm.available) / 2**30)
            self.peak_strata = max(self.peak_strata, self.strata_gb())
            self._stop.wait(2)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(5)

    def result(self) -> dict:
        return {"system_used_peak_gb": round(self.peak_sys, 1), "strata_private_peak_gb": round(self.peak_strata, 1)}


# ---------------------------------------------------------------- Strata control

def launch_args(pack: str, fast: bool) -> list[str]:
    p = PACKS[pack]
    args = ["launch", "--family", p["family"], "--model", p["model"], "--context", "32k", "--vision", "off",
            "--tune", "skip", "--access", "local", "--port", str(PORT), "--data-dir", DATA, "--yes", "--no-tui"]
    if fast:
        for k, v in FAST_SET.items():
            args += ["--set", f"{k}={v}"]
    return args


def ready() -> bool:
    try:
        with urllib.request.urlopen(URL + "/v1/models", timeout=10) as f:
            return f.status == 200
    except OSError:
        return False


def served_ids() -> list[str]:
    try:
        with urllib.request.urlopen(URL + "/v1/models", timeout=10) as f:
            return [m.get("id") for m in json.loads(f.read()).get("data", [])]
    except OSError:
        return []


def strata_stop() -> dict:
    r = subprocess.run(["cmd", "/c", T.STRATA_CMD, "launch", "--stop"], capture_output=True, text=True, timeout=300)
    t0 = time.time()
    while ready() and time.time() - t0 < 120:
        time.sleep(3)
    time.sleep(5)
    return {"exit": r.returncode, "port_closed": not ready(),
            "free_ram_gb_after": round(psutil.virtual_memory().available / 2**30, 1)}


def strata_start(pack: str, fast: bool, logdir: Path) -> dict:
    meta = {"ollama_unloaded": T.ollama_unload_all()}
    time.sleep(10)
    meta["free_ram_gb_before_start"] = round(psutil.virtual_memory().available / 2**30, 1)
    if ready():
        raise SystemExit("port 8090 is already serving something - refusing to start")
    logdir.mkdir(parents=True, exist_ok=True)
    logf = open(logdir / f"strata-launch-{pack}{'-fast' if fast else ''}.log", "ab")
    args = launch_args(pack, fast)
    meta["command"] = " ".join([T.STRATA_CMD, *args])
    t0 = time.time()
    subprocess.Popen(["cmd", "/c", T.STRATA_CMD, *args], stdout=logf, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=0x00000008 | 0x00000200)
    while time.time() - t0 < 1500:
        if ready():
            meta["load_time_s"] = round(time.time() - t0, 1)
            time.sleep(15)            # GEMM warmup after the port opens
            meta["served_model_ids"] = served_ids()
            return meta
        time.sleep(3)
    meta["load_error"] = "not ready in 25 min"
    return meta


# ---------------------------------------------------------------- speed

_WORDS = ("invoice shipment contract delivery warehouse Prague Brno customer supplier pallet route driver payment "
          "deadline audit report quarter budget forecast margin tender clause penalty insurance freight customs").split()


def filler(n_words: int, salt: str) -> str:
    out, i = [], 0
    while len(out) < n_words:
        w = _WORDS[(i * 7 + len(out)) % len(_WORDS)]
        out.append(f"{w}{(i * 131) % 997}" if i % 5 == 0 else w)
        i += 1
        if i % 18 == 0:
            out[-1] += "."
    return f"[run {salt}] " + " ".join(out)


def one_speed(target_tokens: int, model: str) -> dict:
    # ~1.6 tokens per filler word with this vocabulary (calibrated on the first run, see usage.prompt_tokens)
    words = int(target_tokens / 1.6)
    prompt = filler(words, uuid.uuid4().hex[:12]) + "\n\nSummarize the text above in three short sentences."
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 256,
            "temperature": 0, "stream": True, "stream_options": {"include_usage": True}}
    req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    ttft, usage, timings = None, None, None
    with urllib.request.urlopen(req, timeout=900) as f:
        for raw in f:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if d == "[DONE]":
                break
            try:
                j = json.loads(d)
            except ValueError:
                continue
            ch = (j.get("choices") or [{}])[0].get("delta") or {}
            if ttft is None and (ch.get("content") or ch.get("reasoning_content") or ch.get("reasoning")):
                ttft = time.time() - t0
            usage = j.get("usage") or usage
            timings = j.get("timings") or timings
    total = time.time() - t0
    pt = (usage or {}).get("prompt_tokens")
    ct = (usage or {}).get("completion_tokens")
    r = {"prompt_tokens": pt, "completion_tokens": ct, "ttft_s": round(ttft or 0, 3), "total_s": round(total, 3),
         "prefill_tok_s_client": round(pt / ttft, 1) if pt and ttft else None,
         "decode_tok_s_client": round((ct - 1) / (total - ttft), 2) if ct and ttft and total > ttft else None,
         "timings": timings}
    if timings:
        r["prefill_tok_s_server"] = timings.get("prompt_per_second")
        r["decode_tok_s_server"] = timings.get("predicted_per_second")
    return r


def speed_suite(model: str) -> dict:
    out = {}
    for label, tgt in (("4k", 4400), ("32k", 30000)):
        runs = []
        for _ in range(2):
            try:
                runs.append(one_speed(tgt, model))
            except Exception as exc:  # noqa: BLE001
                runs.append({"error": f"{type(exc).__name__}: {exc}"[:300]})
        ok = [x for x in runs if "error" not in x]

        def med(k):
            v = [x.get(k) for x in ok if x.get(k)]
            return round(statistics.median(v), 1) if v else None
        out[label] = {"runs": runs, "prefill_tok_s": med("prefill_tok_s_server") or med("prefill_tok_s_client"),
                      "decode_tok_s": med("decode_tok_s_server") or med("decode_tok_s_client")}
        log(f"speed {label}: prefill {out[label]['prefill_tok_s']} decode {out[label]['decode_tok_s']}")
    return out


# ---------------------------------------------------------------- Bossman model entries

def ensure_model(api: runner.Api, pack: str) -> int:
    p = PACKS[pack]
    if p.get("model_id"):
        return int(p["model_id"])
    name = f"qwen3.8-flash-next-{pack.replace('_', '-')}"
    alias = f"strata-qwen38-flash-next-{pack.replace('_', '')}"
    items = api.req("GET", "/models")[1]
    items = items.get("items", items) if isinstance(items, dict) else items
    for m in items:
        if m.get("alias") == alias or (m.get("name") == name and int(m.get("provider_id") or 0) == PROVIDER):
            return int(m["id"])
    code, row = api.req("POST", "/models", {"provider_id": PROVIDER, "name": name, "alias": alias, "kind": "local",
                                             "context_window": 32768,
                                             "caps": {"tools": True, "reasoning": True, "vision": False},
                                             "price_in": 0, "price_out": 0})
    if code >= 300:
        raise SystemExit(f"cannot register model for {pack}: {code} {row}")
    return int(row["id"])


# ---------------------------------------------------------------- one pack

def arm_name(pack: str, fast: bool) -> str:
    return f"{pack}_fast" if fast else pack


def run_pack(pack: str, fast: bool, score_ids: list[str] | None, st: dict) -> dict:
    api = runner.Api()
    arm = arm_name(pack, fast)
    rec = st["packs"].setdefault(arm, {"pack": pack, "fast": fast})
    rec["model_id"] = ensure_model(api, pack)
    with RamPeak() as ram:
        meta = strata_start(pack, fast, OUT_RUN / "logs")
        rec["runtime"] = meta
        rec["ram_after_load"] = ram.result()
        save_state(st)
        try:
            if "load_error" in meta:
                return rec
            rec["speed"] = speed_suite(f"qwen3.8-flash-next-{pack.replace('_', '-')}")
            save_state(st)
            if score_ids is not None:
                T.ARMS.clear()
                T.ARMS[arm] = {"label": arm, "model_id": rec["model_id"],
                               "title": f"Strata Qwen3.8-Flash-Next {pack}{' fast' if fast else ''}",
                               "runtime": "strata"}
                # our own start/stop wrap the whole pack; run_arm's hooks become no-ops
                T.strata_start = lambda m: None
                T.strata_stop = lambda m: None
                orig = T.sample
                T.sample = lambda: [s for s in fixed_sample() if s["id"] in set(score_ids)]
                t0 = time.time()
                try:
                    rec["arm_result"] = T.run_arm(arm)
                finally:
                    T.sample = orig
                rec["scoring_wall_s"] = round(time.time() - t0, 1)
                save_state(st)
        finally:
            rec["ram_peak"] = ram.result()
            rec["stop"] = strata_stop()
            save_state(st)
    return rec


def run_all(include: list[str]) -> None:
    st = load_state()
    st["started_at"] = st.get("started_at") or time.strftime("%Y-%m-%dT%H:%M:%S")
    ids = [s["id"] for s in fixed_sample()]
    st["sample_ids"] = ids
    used = sum(r.get("scoring_wall_s", 0) for r in st["packs"].values())
    per_pack_est = None
    for pack in include:
        if pack in st["packs"] and st["packs"][pack].get("stop"):
            continue
        left = SCORING_BUDGET_S - used
        est = per_pack_est or 15 * 60
        # keep room for the fast-config sanity run (same 14 ids) after the packs
        score = left - est >= est
        log(f"pack {pack}: scoring {'yes' if score else 'NO (budget)'}; used {used:.0f}s est {est:.0f}s")
        rec = run_pack(pack, False, ids if score else None, st)
        if score and rec.get("scoring_wall_s"):
            used += rec["scoring_wall_s"]
            per_pack_est = max(per_pack_est or 0, rec["scoring_wall_s"])
        st["scoring_used_s"] = used
        save_state(st)
    st["packs_done_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_state(st)


def run_fast(pack: str) -> None:
    st = load_state()
    ids = [s["id"] for s in fixed_sample()]
    run_pack(pack, True, ids, st)
    st["fast_done_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_state(st)


def set_arms_from_state() -> None:
    st = load_state()
    T.ARMS.clear()
    for arm, r in st["packs"].items():
        if r.get("arm_result"):
            T.ARMS[arm] = {"label": arm, "model_id": r["model_id"], "title": arm, "runtime": "strata"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["run", "fast", "judge", "sample"])
    ap.add_argument("--packs", default="iq3_s,ud_iq4_xs,ud_q4_k_xl,iq3_xxs")
    ap.add_argument("--pack")
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args(argv)
    if a.mode == "sample":
        print(json.dumps([s["id"] for s in fixed_sample()]))
    elif a.mode == "run":
        run_all(a.packs.split(","))
    elif a.mode == "fast":
        run_fast(a.pack)
    elif a.mode == "judge":
        while True:
            set_arms_from_state()
            vd = T._load(T.verdicts_path(), None)
            if vd is not None:                 # judge_all only creates arm keys when it creates the file
                for arm in T.ARMS:
                    vd["verdicts"].setdefault(arm, {})
                T._save(T.verdicts_path(), vd)
            r = T.judge_all()
            log(f"judge pass: {r}")
            if not a.loop or r["unjudged"] == 0:
                break
            time.sleep(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
