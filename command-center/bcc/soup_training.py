"""Optional local Soup adapter. Planning does not train or promote model weights."""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

VERSION = "0.76.0"
UPSTREAM = "https://github.com/MakazhanAlpamys/Soup"
UPSTREAM_SHA = "5349282ef22d1d6bece5e88fdfaf9689f2c538b4"
LIMIT = 8 * 1024 * 1024


def offline_env() -> dict[str, str]:
    """Do not hand the training process provider tokens or network credentials."""
    env = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE") if k in os.environ}
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
               WANDB_DISABLED="true", PYTHONUTF8="1", PYTHONNOUSERSITE="1")
    return env


def interpreter() -> Path | None:
    raw = os.environ.get("BOSSMAN_SOUP_PYTHON", "")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() and path.is_file() else None


def status() -> dict:
    result = {"backend": "soup", "pinned_version": VERSION, "upstream": UPSTREAM,
              "upstream_sha": UPSTREAM_SHA, "license": "Apache-2.0", "scope": "local_llm_planner_sft",
              "video_diffusion_training": False, "weights_trained": False,
              "status": "NOT_INSTALLED", "training_ready": False}
    python = interpreter()
    if python is None:
        result["reason"] = "Configure BOSSMAN_SOUP_PYTHON with an isolated Python 3.10–3.12 environment."
        return result
    probe = ("import importlib.metadata as m,importlib.util as u,json,sys;"
             "print(json.dumps({'version':m.version('soup-cli'),'python':list(sys.version_info[:2]),"
             "'missing':[n for n in ['torch','transformers','peft','trl','datasets','accelerate','bitsandbytes'] if u.find_spec(n) is None]}))")
    try:
        proc = subprocess.run([str(python), "-I", "-c", probe], capture_output=True,
                              text=True, timeout=15, env=offline_env(), shell=False)
        if proc.returncode:
            result["reason"] = "Configured environment does not provide soup-cli."
            return result
        info = json.loads(proc.stdout)
        result.update(installed_version=info["version"], missing_training_modules=info["missing"])
        if info["version"] != VERSION or not (3, 10) <= tuple(info["python"]) < (3, 13):
            result.update(status="INCOMPATIBLE", reason="Pinned version or supported Python mismatch.")
        elif info["missing"]:
            result.update(status="CLI_ONLY", reason="Install pinned [train] extra in the isolated environment; hardware remains unverified.")
        else:
            result.update(status="DEPENDENCIES_PRESENT", reason="GPU/backend compatibility and training gain are NOT_VERIFIED.")
        # Imports/package presence are never sufficient evidence of usable hardware.
        return result
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        result.update(status="PROBE_FAILED", reason="Bounded offline environment probe failed.")
        return result


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_path(value: str, roots: list[Path], *, directory: bool = False) -> Path:
    path = Path(value).expanduser().resolve(strict=True)
    if not any(path.is_relative_to(root.resolve()) for root in roots):
        raise ValueError("Path is outside the calling task's allowed roots.")
    if directory and not path.is_dir() or not directory and not path.is_file():
        raise ValueError("Wrong path type.")
    return path


def read_examples(path: Path) -> tuple[list[dict], set[str], set[str]]:
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Dataset exceeds the 8 MiB preparation limit.")
    rows, groups, hashes = [], set(), set()
    for line in raw.decode("utf-8-sig").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("Each JSONL row must be an object.")
        source = item.get("source_id")
        messages = item.get("messages")
        if not isinstance(source, str) or not source.strip() or len(source) > 256:
            raise ValueError("Every example needs a source_id (original session/video, not frame ID).")
        if not isinstance(messages, list) or not 2 <= len(messages) <= 64:
            raise ValueError("Expected 2–64 messages per example.")
        normalized = []
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"}:
                raise ValueError("Expected system/user/assistant chat messages.")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 32000:
                raise ValueError("Message content must be nonempty bounded text.")
            normalized.append({"role": message["role"], "content": content})
        if normalized[-1]["role"] != "assistant" or not any(m["role"] == "user" for m in normalized):
            raise ValueError("An example needs a user and a final assistant target.")
        row = {"messages": normalized}
        fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if fingerprint in hashes:
            raise ValueError("Duplicate example within dataset.")
        rows.append(row)
        groups.add(source)
        hashes.add(fingerprint)
        if len(rows) > 5000:
            raise ValueError("Dataset exceeds 5000 examples.")
    if len(rows) < 2:
        raise ValueError("Each split needs at least two examples; this is not a quality sufficiency claim.")
    return rows, groups, hashes


def prepare(*, base: str, train: str, holdout: str, roots: list[Path], output_root: Path) -> dict:
    model = local_path(base, roots, directory=True)
    model_config = model / "config.json"
    if not model_config.is_file():
        raise ValueError("Base must be a local Transformers model directory with config.json.")
    with model_config.open("rb") as stream:
        model_meta = json.loads(stream.read(1024 * 1024))
    if not isinstance(model_meta, dict) or model_meta.get("auto_map"):
        raise ValueError("Remote/custom model code is not admitted by this adapter.")
    train_path, holdout_path = local_path(train, roots), local_path(holdout, roots)
    training, train_groups, train_hashes = read_examples(train_path)
    evaluation, eval_groups, eval_hashes = read_examples(holdout_path)
    if train_groups & eval_groups or train_hashes & eval_hashes:
        raise ValueError("Train/holdout leakage: shared source_id or duplicated content.")
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    job = root / secrets.token_hex(12)
    job.mkdir(mode=0o700)
    for name, rows in (("train.jsonl", training), ("holdout.jsonl", evaluation)):
        (job / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    config = {"base": str(model), "task": "sft", "modality": "text",
              "data": {"train": str(job / "train.jsonl"), "format": "chatml", "val_split": .1, "max_length": 1024},
              "training": {"epochs": 1, "lr": 2e-4, "batch_size": 1, "seed": 42,
                           "quantization": "none", "lora": {"r": 8, "alpha": 16, "dropout": .05}},
              "output": str(job / "candidate")}
    # JSON is a YAML subset: no custom YAML constructors, interpolations or executable config.
    config_path = job / "soup.yaml"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"schema": "bossman.soup-plan/1", "status": "PREPARED_NOT_TRAINED", "soup_version": VERSION,
                "train_rows": len(training), "holdout_rows": len(evaluation),
                "split": "source-disjoint external holdout; Soup internal validation is not the final independent evaluation",
                "files": {name: digest(job / name) for name in ("train.jsonl", "holdout.jsonl", "soup.yaml")},
                "promotion": "NOT_ALLOWED_WITHOUT_INDEPENDENT_EVALUATION", "video_diffusion_training": False}
    manifest_path = job / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest_sha = digest(manifest_path)
    # Execute through Bossman's interpreter/module so file hashes and offline env are enforced.
    prefix = [sys.executable, "-m", "bcc.soup_training"]
    suffix = ["--job", str(job), "--manifest-sha256", manifest_sha]
    return {**manifest, "job_dir": str(job), "preflight": status(),
            "manifest_sha256": manifest_sha,
            "doctor_argv": prefix + ["doctor"] + suffix,
            "train_argv": prefix + ["train"] + suffix,
            "execution": "Use existing terminal.run (project_host, network=false) after owner approval; monitor/STOP through terminal.status/terminal.stop. No automatic execution."}


def run_job(action: str, job: Path, expected_manifest: str) -> int:
    """Called ONLY as an explicitly approved terminal command; no server process spawn."""
    job = job.resolve(strict=True)
    if action not in {"doctor", "train"} or digest(job / "manifest.json") != expected_manifest:
        raise ValueError("Job manifest changed since preparation/approval.")
    manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "bossman.soup-plan/1" or manifest.get("soup_version") != VERSION:
        raise ValueError("Unsupported manifest.")
    for name in ("train.jsonl", "holdout.jsonl", "soup.yaml"):
        if digest(job / name) != manifest["files"][name]:
            raise ValueError("Job input changed since preparation/approval.")
    config = json.loads((job / "soup.yaml").read_text(encoding="utf-8"))
    if config.get("task") != "sft" or config.get("modality") != "text":
        raise ValueError("Only text planner SFT is supported.")
    if Path(config["data"]["train"]).resolve() != job / "train.jsonl" or Path(config["output"]).resolve() != job / "candidate":
        raise ValueError("Job paths changed.")
    base = Path(config["base"])
    if not base.is_absolute() or not base.is_dir():
        raise ValueError("Base must remain local.")
    with (base / "config.json").open("rb") as stream:
        model_meta = json.loads(stream.read(1024 * 1024))
        if not isinstance(model_meta, dict) or model_meta.get("auto_map"):
            raise ValueError("Custom model code is not supported.")
    info = status()
    if info["status"] not in {"CLI_ONLY", "DEPENDENCIES_PRESENT"}:
        raise ValueError("Pinned Soup environment is unavailable.")
    if action == "train" and info["status"] != "DEPENDENCIES_PRESENT":
        raise ValueError("Training dependencies missing; no training started.")
    argv = [str(interpreter()), "-I", "-c", "from soup_cli.cli import run; run()", action, "--config", str(job / "soup.yaml")]
    return subprocess.call(argv, cwd=job, env=offline_env(), shell=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "doctor", "train"])
    parser.add_argument("--job", type=Path)
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    if args.action == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if args.job is None or not args.manifest_sha256:
        parser.error("--job and --manifest-sha256 are required")
    try:
        return run_job(args.action, args.job, args.manifest_sha256)
    except (ValueError, OSError, KeyError, TypeError):
        print("Soup job rejected: environment unavailable or approved local inputs changed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
