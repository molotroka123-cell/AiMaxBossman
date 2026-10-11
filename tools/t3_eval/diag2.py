import os, sys, time
os.environ['MIOPEN_FIND_MODE'] = os.environ.get('MFM', 'FAST')
import torch
from diffsynth.pipelines.wan_video_new import WanVideoPipeline, ModelConfig
W2 = r'C:\Users\asd\Bossman\models\media\t3-video-4k\wan21-t2v-1.3b'
M = 'Wan-AI/Wan2.1-T2V-1.3B'
cfgs = [ModelConfig(model_id=M, origin_file_pattern=W2 + r'\Wan2.1_VAE.pth', local_model_path='models', offload_device='cpu')]
pipe = WanVideoPipeline.from_pretrained(torch_dtype=torch.bfloat16, device='cuda', model_configs=cfgs, redirect_common_files=False)
def st(n, t):
    t = t.float(); print(f'{n}: shape={tuple(t.shape)} nan={torch.isnan(t).any().item()} min={t.nan_to_num().min().item():.3f} max={t.nan_to_num().max().item():.3f} std={t.nan_to_num().std().item():.3f}', flush=True)
torch.manual_seed(0)
lat = torch.randn(16, 3, 32, 56)  # (C,T,H,W) latent -> 9 frames 256x448
for dev, dt in [(sys.argv[1], getattr(torch, sys.argv[2]))]:
    pipe.vae.model.to(dev, dtype=dt)
    t = time.time()
    with torch.no_grad():
        out = pipe.vae.decode(lat.unsqueeze(0).to(dev, dtype=dt), device=dev, tiled=False)
    if dev == 'cuda': torch.cuda.synchronize()
    print(dev, sys.argv[2], 'decode_s', round(time.time() - t, 1), flush=True)
    st('out', out if torch.is_tensor(out) else out[0])
