set -e
exec > >(tee -a /workspace/setup.log) 2>&1
cd /workspace
[ -d sd-scripts ] || git clone --depth 1 https://github.com/kohya-ss/sd-scripts.git
cd sd-scripts && pip install -q -r requirements.txt && cd ..
pip install -q "huggingface_hub[cli]"
huggingface-cli download stabilityai/stable-diffusion-xl-base-1.0 sd_xl_base_1.0.safetensors --local-dir /workspace/models/sdxl-base
accelerate config default || true
echo SETUP_DONE
