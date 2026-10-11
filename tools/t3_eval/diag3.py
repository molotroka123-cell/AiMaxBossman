import os, sys, time
mode, aot, H, W, T = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
os.environ['T3_TOK_H'] = str(H // 16); os.environ['T3_TOK_W'] = str(W // 16); os.environ['T3_MODE'] = 'full' if mode == 'control' else 't3'
if aot == '1': os.environ['TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL'] = '1'
import torch
from diffsynth import load_state_dict
from diffsynth.pipelines.wan_video_new import WanVideoPipeline, ModelConfig
B = r'C:\Users\asd\Bossman\models\media\t3-video-4k'
cfgs = [ModelConfig(model_id='Wan-AI/Wan2.1-T2V-1.3B', origin_file_pattern=B + r'\wan21-t2v-1.3b\diffusion_pytorch_model.safetensors', local_model_path='models', offload_device='cpu')]
pipe = WanVideoPipeline.from_pretrained(torch_dtype=torch.bfloat16, device='cuda', model_configs=cfgs, redirect_common_files=False)
dit = pipe.dit
if mode == 't3':
    sd = load_state_dict(B + r'\t3\T3-Video-Wan2.1-T2V-1.3B.safetensors')
    r = dit.load_state_dict(sd); print('load', r, flush=True)
    print('t3 dtype sample', next(iter(sd.values())).dtype)
dit.to('cuda', dtype=torch.bfloat16).eval()
torch.manual_seed(0)
x = torch.randn(1, 16, T, H // 8, W // 8, device='cuda', dtype=torch.bfloat16)
ctx = torch.randn(1, 512, 4096, device='cuda', dtype=torch.bfloat16)
ts = torch.tensor([500.], device='cuda', dtype=torch.bfloat16)
t = time.time()
with torch.no_grad():
    y = dit(x, ts, ctx)
torch.cuda.synchronize()
y = y.float()
print(mode, 'aot', aot, 'time', round(time.time() - t, 1), 'nan', torch.isnan(y).any().item(), 'inf', torch.isinf(y).any().item(), 'std', y.nan_to_num().std().item(), 'absmax', y.nan_to_num().abs().max().item(), flush=True)
