#!/usr/bin/env python3
"""Evaluation runner for T3-Video plan 2026-10-11. One process = one rung, prompts P1..P3 sequentially.
Usage: run_eval.py --mode t3|control --width W --height H --out DIR [--tiled] [--only P1]
"""
import os, sys, json, time, argparse, threading, traceback
ap = argparse.ArgumentParser()
ap.add_argument('--mode', required=True, choices=['t3', 'control'])
ap.add_argument('--width', type=int, required=True)
ap.add_argument('--height', type=int, required=True)
ap.add_argument('--out', required=True)
ap.add_argument('--tiled', action='store_true')
ap.add_argument('--only', default='')
ap.add_argument('--vae', default='gpu', choices=['gpu','cpu'])
ap.add_argument('--steps', type=int, default=15)
ap.add_argument('--cap', type=int, default=1500)
a = ap.parse_args()
os.environ['T3_TOK_H'] = str(a.height // 16)
os.environ['T3_TOK_W'] = str(a.width // 16)
os.environ['T3_MODE'] = 'full' if a.mode == 'control' else 't3'
os.environ.setdefault('MIOPEN_FIND_MODE', 'FAST')
import torch, psutil, imageio
from PIL import Image
from diffsynth import load_state_dict
from diffsynth.pipelines.wan_video_new import WanVideoPipeline, ModelConfig

BASE = r'C:\Users\asd\Bossman\models\media\t3-video-4k'
W2 = BASE + r'\wan21-t2v-1.3b'
PROMPTS = {
 'P1': (20261011, "A wide landscape of a snow-capped mountain valley at sunrise, a turquoise river winding through dark pine forest, light mist drifting over the water, golden light on the peaks, static camera, cinematic, highly detailed."),
 'P2': (20261012, "A young woman with long dark hair slowly turns her head from left profile toward the camera and smiles slightly, soft window light, plain grey wall behind her, static camera, shallow depth of field, photorealistic portrait."),
 'P3': (20261013, "A rainy neon-lit city street at night, pink and blue glowing signs reflected on wet asphalt, a few people with umbrellas walking, a car passes slowly, static camera, cinematic."),
}
NEG = "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
os.makedirs(a.out, exist_ok=True)
proc = psutil.Process()
peak = {'rss_gb': 0.0, 'sys_used_gb': 0.0, 'gpu_free_min_gb': 1e9, 'swap_used_max_gb': 0.0}
stop = threading.Event()
phase = {'t0': None}
def sampler():
    while not stop.is_set():
        try:
            peak['rss_gb'] = max(peak['rss_gb'], proc.memory_info().rss / 2**30)
            vm = psutil.virtual_memory(); peak['sys_used_gb'] = max(peak['sys_used_gb'], vm.used / 2**30)
            peak['swap_used_max_gb'] = max(peak['swap_used_max_gb'], psutil.swap_memory().used / 2**30)
            f, t = torch.cuda.mem_get_info(); peak['gpu_free_min_gb'] = min(peak['gpu_free_min_gb'], f / 2**30); peak['gpu_total_gb'] = t / 2**30
        except Exception:
            pass
        if phase['t0'] and time.time() - phase['t0'] > a.cap:
            print('WATCHDOG: cap exceeded, killing', flush=True)
            json.dump({'status': 'TIMEOUT', 'peak': peak, 'cap': a.cap}, open(os.path.join(a.out, 'TIMEOUT.json'), 'w'))
            os._exit(3)
        time.sleep(2)
threading.Thread(target=sampler, daemon=True).start()

t_load = time.time()
cfgs = [ModelConfig(model_id='Wan-AI/Wan2.1-T2V-1.3B', origin_file_pattern=W2 + r'\models_t5_umt5-xxl-enc-bf16.pth', local_model_path='models', offload_device='cpu'),
        ModelConfig(model_id='Wan-AI/Wan2.1-T2V-1.3B', origin_file_pattern=W2 + r'\diffusion_pytorch_model.safetensors', local_model_path='models', offload_device='cpu'),
        ModelConfig(model_id='Wan-AI/Wan2.1-T2V-1.3B', origin_file_pattern=W2 + r'\Wan2.1_VAE.pth', local_model_path='models', offload_device='cpu')]
pipe = WanVideoPipeline.from_pretrained(torch_dtype=torch.bfloat16, device='cuda', model_configs=cfgs, redirect_common_files=False)
if a.mode == 't3':
    pipe.dit.load_state_dict(load_state_dict(BASE + r'\t3\T3-Video-Wan2.1-T2V-1.3B.safetensors'))
pipe.enable_vram_management()
load_s = time.time() - t_load
print('loaded', load_s, flush=True)
vae_t = {'s': 0.0}
_orig = pipe.vae.decode
def _st(n, x):
    x = x.float(); print(f'[stat] {n} shape={tuple(x.shape)} nan={torch.isnan(x).any().item()} inf={torch.isinf(x).any().item()} min={x.nan_to_num().min().item():.3f} max={x.nan_to_num().max().item():.3f} std={x.nan_to_num().std().item():.3f}', flush=True)
def _dec(*aa, **kk):
    _st('vae_in', aa[0]); t = time.time()
    if a.vae == 'cpu':
        pipe.vae.model.to('cpu')
        kk = dict(kk); kk['device'] = 'cpu'
        r = _orig(aa[0].detach().cpu(), **kk)
    else:
        r = _orig(*aa, **kk)
    torch.cuda.synchronize(); vae_t['s'] += time.time() - t; _st('vae_out', r); print('[stat] vae_s', vae_t['s'], flush=True); return r
pipe.vae.decode = _dec

summary = {'mode': a.mode, 'width': a.width, 'height': a.height, 'steps': a.steps, 'tiled': a.tiled, 'load_s': load_s, 'runs': {}}
ratio = 2
for pid, (seed, prompt) in PROMPTS.items():
    if a.only and pid != a.only:
        continue
    d = os.path.join(a.out, pid); os.makedirs(d, exist_ok=True)
    vae_t['s'] = 0.0
    torch.cuda.reset_peak_memory_stats()
    base_peak = dict(peak)
    phase['t0'] = time.time()
    rec = {'prompt': prompt, 'seed': seed}
    try:
        video = pipe(prompt=prompt, negative_prompt=NEG, seed=seed, height=a.height, width=a.width, num_frames=81,
                     tiled=a.tiled, tile_size=(34 * ratio, 60 * ratio), tile_stride=(17 * ratio, 30 * ratio),
                     cfg_scale=5.0, cfg_merge=False, num_inference_steps=a.steps)
        torch.cuda.synchronize()
        wall = time.time() - phase['t0']
        phase['t0'] = None
        rec.update(status='OK', wall_s=wall, vae_decode_s=vae_t['s'], denoise_s=wall - vae_t['s'], s_per_frame=wall / 81,
                   n_frames=len(video), size=list(video[0].size), torch_max_alloc_gb=torch.cuda.max_memory_allocated() / 2**30)
        w = imageio.get_writer(os.path.join(d, 'clip.mp4'), fps=27, codec='libx264', quality=None, macro_block_size=1,
                               ffmpeg_params=['-crf', '14', '-pix_fmt', 'yuv420p'])
        for f in video: w.append_data(__import__('numpy').asarray(f))
        w.close()
        for i in (0, 20, 40, 60, 80):
            video[i].save(os.path.join(d, f'frame_{i:02d}.png'))
    except Exception as e:
        phase['t0'] = None
        rec.update(status='FAIL', error=repr(e)[:800], trace=traceback.format_exc()[-1500:], wall_s=time.time() - (phase['t0'] or time.time()))
        print('FAIL', pid, repr(e), flush=True)
    rec['peak_during_rung_so_far'] = dict(peak)
    summary['runs'][pid] = rec
    json.dump(summary, open(os.path.join(a.out, 'summary.json'), 'w'), indent=1)
    print(pid, rec.get('status'), rec.get('wall_s'), flush=True)
    if rec['status'] != 'OK':
        break
stop.set()
