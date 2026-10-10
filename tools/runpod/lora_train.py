#!/usr/bin/env python3
"""RunPod SDXL LoRA training (kohya sd-scripts) - the recipe proven on 09.10 / 10.10.2026.

One pod, several LoRAs one after another, always terminated at the end.

    python tools/runpod/lora_train.py deploy   --env KEYFILE --ssh-key ID --watchdog-min 180
    python tools/runpod/lora_train.py setup    # kohya + torch 2.6 fix, runs detached on the pod
    python tools/runpod/lora_train.py upload   --model BASE.safetensors --dataset DIR_WITH_JOB_FOLDERS
    python tools/runpod/lora_train.py train    --job gfgirl:1500:2:1e-4:500 --job kisliy:1200:1:6e-5:300
    python tools/runpod/lora_train.py status
    python tools/runpod/lora_train.py fetch    --out OUTDIR            # sha256-verified download
    python tools/runpod/lora_train.py terminate                        # pods [] / spend 0 / volumes [] proof

State (pod id, ssh target) lives in --state (default ./.lora_pod_state.json), never the API key.
The key is read from the env file line RUNPOD_API_KEY=... or the RUNPOD_API_KEY variable and is never printed.
Dataset layout (kohya): DIR/<job>/<repeats>_<name>/*.jpg + *.txt (caption, trigger first).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

GQL = "https://api.runpod.io/graphql"
IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"   # NO dockerArgs: overriding start kills sshd
GPUS = [("SECURE", "NVIDIA A100 80GB PCIe"), ("SECURE", "NVIDIA A100-SXM4-80GB"),
        ("COMMUNITY", "NVIDIA A100 80GB PCIe"), ("COMMUNITY", "NVIDIA A100-SXM4-80GB"),
        ("SECURE", "NVIDIA GeForce RTX 4090"), ("COMMUNITY", "NVIDIA GeForce RTX 4090")]
MIN_NET_MBS = 10.0   # community hosts can be 145 KB/s: test before installing anything

POD_SETUP = r"""#!/bin/bash
exec > /workspace/setup.log 2>&1
set -x
cd /workspace; mkdir -p models dataset output
[ -d sd-scripts ] || git clone --depth 1 https://github.com/kohya-ss/sd-scripts.git
cd sd-scripts
pip install -q -r requirements.txt || echo PIPFAIL
# sd-scripts HEAD pins transformers 5.x / diffusers 0.40: they need torch >= 2.5, the image ships 2.4.x
pip install -q torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
pip uninstall -y -q torchaudio || true      # stale torchaudio .so breaks the import (undefined symbol)
accelerate config default || true
python -c "import library.train_util, library.sdxl_train_util, torch; print('IMPORT_OK', torch.__version__, torch.cuda.is_available())"
echo SETUP_DONE
"""

TRAIN_SH = r"""#!/bin/bash
cd /workspace/sd-scripts
MODEL=/workspace/models/base.safetensors
run() { # name steps batch lr save_every
  NAME=$1; STEPS=$2; BS=$3; LR=$4; SAVE=$5
  echo "=== START $NAME $(date +%T)" >> /workspace/train_all.log
  accelerate launch --num_cpu_threads_per_process 4 --mixed_precision=bf16 sdxl_train_network.py \
    --pretrained_model_name_or_path=$MODEL \
    --train_data_dir=/workspace/dataset/$NAME --output_dir=/workspace/output/$NAME --output_name=$NAME-lora \
    --resolution=1024,1024 --enable_bucket --min_bucket_reso=512 --max_bucket_reso=1536 --bucket_reso_steps=64 \
    --network_module=networks.lora --network_dim=16 --network_alpha=16 --network_train_unet_only \
    --learning_rate=$LR --unet_lr=$LR --optimizer_type=AdamW8bit --lr_scheduler=cosine --lr_warmup_steps=50 \
    --max_train_steps=$STEPS --save_every_n_steps=$SAVE --train_batch_size=$BS \
    --mixed_precision=bf16 --save_precision=bf16 --save_model_as=safetensors \
    --sdpa --caption_extension=.txt --cache_latents --cache_latents_to_disk --cache_text_encoder_outputs \
    --max_data_loader_n_workers=4 --persistent_data_loader_workers --seed=42 \
    > /workspace/train_$NAME.log 2>&1
  echo "=== END $NAME rc=$? $(date +%T)" >> /workspace/train_all.log
}
"""


def _key(env_file: str | None) -> str:
    if os.environ.get("RUNPOD_API_KEY"):
        return os.environ["RUNPOD_API_KEY"].strip()
    if env_file:
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("RUNPOD_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("no RUNPOD_API_KEY (env var or --env file)")


class Pod:
    def __init__(self, args):
        self.args = args
        self.state_path = Path(args.state)
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.key = _key(getattr(args, "env", None) or self.state.get("env"))

    def save(self, **kw):
        self.state.update(kw)
        self.state_path.write_text(json.dumps(self.state))

    def gql(self, q: str) -> dict:
        r = urllib.request.Request(GQL, data=json.dumps({"query": q}).encode(), headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.key, "User-Agent": "bossman-lora/1"})
        return json.load(urllib.request.urlopen(r, timeout=60))

    # ---- ssh helpers -------------------------------------------------------------------------
    def _opts(self):
        return ["-i", self.state["ssh_key"], "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR",
                "-o", "ServerAliveInterval=20"]

    def ssh(self, cmd: str, timeout=600) -> subprocess.CompletedProcess:
        return subprocess.run(["ssh", *self._opts(), "-p", str(self.state["port"]), f"root@{self.state['ip']}", cmd],
                              capture_output=True, text=True, timeout=timeout)

    def scp_up(self, local: str, remote: str, recursive=False):
        cmd = ["scp", "-q", *(["-r"] if recursive else []), *self._opts(), "-P", str(self.state["port"]), local,
               f"root@{self.state['ip']}:{remote}"]
        subprocess.run(cmd, check=True)

    def scp_down(self, remote: str, local: str):
        subprocess.run(["scp", "-q", *self._opts(), "-P", str(self.state["port"]),
                        f"root@{self.state['ip']}:{remote}", local], check=True)

    # ---- commands ----------------------------------------------------------------------------
    def deploy(self):
        a = self.args
        self.save(ssh_key=a.ssh_key, env=a.env)
        # the account must already hold the matching .pub (updateUserSettings pubKey); PUBLIC_KEY env var does not work
        for rnd in range(3):
            for cloud, gpu in GPUS:
                q = ('mutation{podFindAndDeployOnDemand(input:{cloudType:%s,gpuCount:1,volumeInGb:80,containerDiskInGb:40,'
                     'minVcpuCount:8,minMemoryInGb:48,gpuTypeId:"%s",name:"lora-train",imageName:"%s",ports:"22/tcp",'
                     'volumeMountPath:"/workspace",startSsh:true,supportPublicIp:true}){id costPerHr}}') % (cloud, gpu, IMAGE)
                p = (self.gql(q).get("data") or {}).get("podFindAndDeployOnDemand")
                if not p:
                    continue
                pid = p["id"]
                self.save(pod_id=pid, gpu=f"{cloud} {gpu}", cost_per_hr=p["costPerHr"], deployed_at=time.time())
                print("deployed", cloud, gpu, p)
                target = None
                for _ in range(60):
                    d = self.gql('query{pod(input:{podId:"%s"}){runtime{ports{ip publicPort privatePort isIpPublic}}}}' % pid)
                    ports = (((d.get("data") or {}).get("pod") or {}).get("runtime") or {}).get("ports") or []
                    s = [x for x in ports if x["privatePort"] == 22 and x["isIpPublic"]]
                    if s:
                        target = s[0]
                        break
                    time.sleep(10)
                ok = False
                if target:
                    self.save(ip=target["ip"], port=target["publicPort"])
                    for _ in range(12):
                        try:
                            o = self.ssh("timeout 25 curl -s -o /dev/null -w '%{speed_download}' -r 0-60000000 -L "
                                         "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/"
                                         "sd_xl_base_1.0.safetensors; echo; nvidia-smi --query-gpu=name --format=csv,noheader", 120)
                            if o.returncode == 0:
                                sp = float(o.stdout.split()[0]) / 1e6
                                print("network MB/s", round(sp, 1), o.stdout.split("\n")[1:2])
                                ok = sp >= MIN_NET_MBS
                                break
                        except Exception:
                            pass
                        time.sleep(10)
                if ok:
                    self.start_watchdog(a.watchdog_min)
                    return
                print("bad host, terminating", pid, self.gql('mutation{podTerminate(input:{podId:"%s"})}' % pid))
        raise SystemExit("no good host")

    def start_watchdog(self, minutes: int):
        """Detached process that terminates the pod after N minutes of wall time no matter what."""
        code = ("import json,time,urllib.request,sys\n"
                f"k={self.key!r};pid={self.state['pod_id']!r};d=time.time()+{int(minutes) * 60}\n"
                f"stop={str(self.state_path.with_suffix('.stop'))!r}\n"
                "import os\n"
                "while time.time()<d:\n"
                "    if os.path.exists(stop): sys.exit(0)\n"
                "    time.sleep(20)\n"
                "r=urllib.request.Request('https://api.runpod.io/graphql',data=json.dumps({'query':'mutation{podTerminate(input:{podId:\"%s\"})}'%pid}).encode(),"
                "headers={'Content-Type':'application/json','Authorization':'Bearer '+k,'User-Agent':'bossman-lora/1'})\n"
                "urllib.request.urlopen(r,timeout=60)\n")
        flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0   # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen([sys.executable, "-c", code], creationflags=flags, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        print(f"watchdog armed: terminate in {minutes} min (touch {self.state_path.with_suffix('.stop')} to disarm)")

    def setup(self):
        tmp = Path(os.environ.get("TEMP", "/tmp")) / "pod_setup.sh"
        tmp.write_text(POD_SETUP, newline="\n")
        self.scp_up(str(tmp), "/workspace/pod_setup.sh")
        self.ssh("chmod +x /workspace/pod_setup.sh; nohup setsid /workspace/pod_setup.sh >/dev/null 2>&1 </dev/null & echo launched")
        print("setup launched; poll: status")

    def upload(self):
        a = self.args
        if a.model:
            self.ssh("mkdir -p /workspace/models")
            self.scp_up(a.model, "/workspace/models/base.safetensors")
            print("model sha256 (local)", _sha256(a.model))
        if a.dataset:
            self.ssh("mkdir -p /workspace/dataset")
            for d in sorted(Path(a.dataset).iterdir()):
                if d.is_dir():
                    self.scp_up(str(d), "/workspace/dataset/", recursive=True)
            print(self.ssh("find /workspace/dataset -type f | wc -l").stdout.strip(), "files on pod")

    def train(self):
        lines = [TRAIN_SH]
        for j in self.args.job:               # name:steps:batch:lr:save_every
            n, steps, bs, lr, save = j.split(":")
            lines.append(f"run {n} {steps} {bs} {lr} {save}\n")
        lines.append("echo ALL_DONE >> /workspace/train_all.log\n")
        tmp = Path(os.environ.get("TEMP", "/tmp")) / "train_all.sh"
        tmp.write_text("".join(lines), newline="\n")
        self.scp_up(str(tmp), "/workspace/train_all.sh")
        self.ssh("chmod +x /workspace/train_all.sh; rm -f /workspace/train_all.log; "
                 "nohup setsid /workspace/train_all.sh >/dev/null 2>&1 </dev/null & echo started")

    def status(self):
        o = self.ssh("tail -3 /workspace/setup.log 2>/dev/null; cat /workspace/train_all.log 2>/dev/null; "
                     "for f in /workspace/train_*.log; do echo $f; tail -c 400 $f | tr '\\r' '\\n' | tail -2; done")
        print(o.stdout[-2500:])

    def fetch(self):
        out = Path(self.args.out)
        out.mkdir(parents=True, exist_ok=True)
        listing = self.ssh("cd /workspace/output && find . -name '*.safetensors' -exec sha256sum {} +").stdout.split("\n")
        for line in filter(None, listing):
            sha, rel = line.split(None, 1)
            rel = rel.strip().lstrip("./").lstrip("*")
            dst = out / rel.replace("/", "_")
            self.scp_down("/workspace/output/" + rel, str(dst))
            got = _sha256(str(dst))
            print(("OK  " if got == sha else "BAD ") + dst.name, got)
            if got != sha:
                raise SystemExit("sha256 mismatch")

    def terminate(self):
        pid = self.state.get("pod_id")
        if pid and self.state.get("ip"):
            try:   # remove private data from the volume first; verified downloads only!
                self.ssh("rm -rf /workspace/dataset /workspace/output /workspace/models", 120)
            except Exception as e:
                print("cleanup ssh failed", e)
        if pid:
            print(self.gql('mutation{podTerminate(input:{podId:"%s"})}' % pid))
        self.state_path.with_suffix(".stop").write_text("stop")
        time.sleep(5)
        m = self.gql("query{myself{currentSpendPerHr pods{id} networkVolumes{id}}}")["data"]["myself"]
        print(json.dumps({"pods": m["pods"], "currentSpendPerHr": m["currentSpendPerHr"], "networkVolumes": m["networkVolumes"]}))
        if m["pods"] or m["currentSpendPerHr"] or m["networkVolumes"]:
            raise SystemExit("NOT CLEAN - terminate again")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=".lora_pod_state.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deploy"); d.add_argument("--env", required=True); d.add_argument("--ssh-key", required=True)
    d.add_argument("--watchdog-min", type=int, default=180)
    sub.add_parser("setup")
    u = sub.add_parser("upload"); u.add_argument("--model"); u.add_argument("--dataset")
    t = sub.add_parser("train"); t.add_argument("--job", action="append", required=True, help="name:steps:batch:lr:save_every")
    sub.add_parser("status")
    f = sub.add_parser("fetch"); f.add_argument("--out", required=True)
    sub.add_parser("terminate")
    args = ap.parse_args()
    getattr(Pod(args), args.cmd)()


if __name__ == "__main__":
    main()
