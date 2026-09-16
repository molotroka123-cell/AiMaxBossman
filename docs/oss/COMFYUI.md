# Local ComfyUI image generation

The Images page now has a real `comfyui` provider. It uses the existing Bossman
queue/library and the upstream ComfyUI HTTP API. It does not install ComfyUI,
model weights, custom nodes or cloud services.

## Windows setup

Use an independently installed and running local ComfyUI service, with a trusted
SD/SDXL-compatible `.safetensors` checkpoint already in its checkpoints folder.
The adapter uses ComfyUI's native `CheckpointLoaderSimple`, `CLIPTextEncode`,
`EmptyLatentImage`, `KSampler`, `VAEDecode` and `SaveImage` nodes. Models requiring
other workflow nodes (for example separate Flux loaders) are not supported by this
first image provider. A `.safetensors` extension alone does not establish model
compatibility or provenance.

In the PowerShell session used to launch Bossman:

```powershell
$env:BOSSMAN_COMFYUI_URL = 'http://127.0.0.1:8188'
$env:BOSSMAN_COMFYUI_CHECKPOINT = 'your-installed-checkpoint.safetensors'
```

Then launch Bossman, open Images, choose **ComfyUI (local text-to-image)**, enter
a prompt and generate. Width and height must be multiples of 8, from 256 to 4096.
Start at 512×512 with one image to verify your installed model and AMD runtime.
The checkpoint field is a filename relative to ComfyUI's checkpoints directory,
not a URL. A local HTTP origin is required; redirects and environment proxies are
not used. Bind ComfyUI to loopback as well.

A model marked `configured` means configuration exists; it does not mean the
service/model/GPU has been verified. The health check proves only that a ComfyUI
service answers. Queue completion requires successful execution history and an
actual downloaded PNG whose dimensions, structure, checksums and decoded scanline
lengths pass validation. The saved library artifact includes its SHA256 and
ComfyUI prompt ID in metadata.

Cancellation prevents the result from being published to the Bossman library.
It does not interrupt other ComfyUI users' work: there is deliberately no global
`/interrupt` call. An in-flight local generation can finish in ComfyUI after a
Bossman cancellation or timeout. Timeout errors retain the prompt ID; inspect the
ComfyUI queue before retrying. There is no automatic resubmission.

Text-to-image only: source images, reference images, editing, video generation,
arbitrary custom nodes and paid/cloud nodes are not advertised by this adapter.
These need separately reviewed workflows. No API keys or model downloads are
required by this integration itself. No model weights are trained.

## Verification

`tests/test_oss_comfyui.py` checks HTTP contracts, a real loopback HTTP service,
path/origin restrictions, execution errors, timeout without duplicate submission,
PNG corruption, real Images-route persistence and cancellation during rendering.
The loopback fixture serves deterministic test PNG data; it is not a GPU/model
quality test. Windows + Radeon generation remains an owner-machine verification.

Upstream API references:

- https://docs.comfy.org/development/comfyui-server/comms_routes
- https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/basic_api_example.py
