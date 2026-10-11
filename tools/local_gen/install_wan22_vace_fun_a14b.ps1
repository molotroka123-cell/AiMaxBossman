# install_wan22_vace_fun_a14b.ps1 - PRIMARY candidate: Wan2.2-VACE-Fun-A14B (Apache-2.0, alibaba-pai) as GGUF Q8_0
# MoE pair + Wan2.2-Lightning 4-step LoRA (Apache-2.0, lightx2v). Runs on the isolated sd.cpp Vulkan build.
# Download budget: high Q8_0 18.7 GB + low Q8_0 18.7 GB + 2 x 1.23 GB LoRA = ~40 GB (< 60 GB owner cap).
# Shared assets reused from existing folders (no copy of the 5.6 GB umt5): see bench_local_gen.py CANDIDATES.
# CPU/network only. Re-runnable: hf skips finished files.
param(
    [string]$MediaRoot = "C:\Users\asd\Bossman\models\media",
    [string]$Quant = "Q8_0",          # Q8_0 (18.7 GB each) or Q6_K (14.5 GB each)
    [switch]$SkipLora
)
$ErrorActionPreference = "Stop"
$dest = Join-Path $MediaRoot "wan22-vace-fun-a14b"
New-Item -ItemType Directory -Force -Path $dest, (Join-Path $dest "loras") | Out-Null
$free = (Get-PSDrive C).Free / 1GB
if ($free -lt 70) { throw "need >= 70 GB free on C: (have $([math]::Round($free,1)) GB)" }
$hf = (Get-Command hf.exe -ErrorAction SilentlyContinue).Source
if (-not $hf) { $hf = (Get-Command huggingface-cli.exe).Source }

# QuantStack naming: high-noise file uses "Q6_k" (lower-case k), low-noise "Q6_K"; Q8_0 is consistent.
$qHigh = $Quant; if ($Quant -eq "Q6_K") { $qHigh = "Q6_k" }
$files = @("HighNoise/Wan2.2-VACE-Fun-A14B-high-noise-$qHigh.gguf", "LowNoise/Wan2.2-VACE-Fun-A14B-low-noise-$Quant.gguf")
Write-Host "[vace-fun] hf download QuantStack/Wan2.2-VACE-Fun-A14B-GGUF $files -> $dest"
& $hf download QuantStack/Wan2.2-VACE-Fun-A14B-GGUF @files --local-dir $dest
if ($LASTEXITCODE -ne 0) { throw "hf download (diffusion) failed" }

if (-not $SkipLora) {
    $sub = "Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V1.1"
    $tmp = Join-Path $dest "loras\_hf"
    & $hf download lightx2v/Wan2.2-Lightning "$sub/high_noise_model.safetensors" "$sub/low_noise_model.safetensors" --local-dir $tmp
    if ($LASTEXITCODE -ne 0) { throw "hf download (lora) failed" }
    # sd-cli resolves <lora:NAME:1> to NAME.safetensors inside --lora-model-dir and routes names containing
    # high_noise / low_noise to the matching Wan2.2 expert, so the two files need distinct names.
    Copy-Item (Join-Path $tmp "$sub\high_noise_model.safetensors") (Join-Path $dest "loras\wan22_lightning_t2v_v1.1_high_noise.safetensors") -Force
    Copy-Item (Join-Path $tmp "$sub\low_noise_model.safetensors") (Join-Path $dest "loras\wan22_lightning_t2v_v1.1_low_noise.safetensors") -Force
    Remove-Item $tmp -Recurse -Force
}
# VAE: Wan2.1 VAE (VACE-Fun A14B uses the 2.1 VAE, not wan2.2_vae); 240 MB copy keeps the folder self-contained.
$vaeSrc = Join-Path $MediaRoot "wan21-vace-1.3b\wan_2.1_vae.safetensors"
if (Test-Path $vaeSrc) { Copy-Item $vaeSrc (Join-Path $dest "wan_2.1_vae.safetensors") -Force } else { Write-Warning "wan_2.1_vae.safetensors not found at $vaeSrc" }

$man = @{}
Get-ChildItem $dest -Recurse -File | Where-Object { $_.Extension -in ".gguf", ".safetensors" } | ForEach-Object {
    $man[$_.FullName.Substring($dest.Length + 1)] = @{ bytes = $_.Length; gb = [math]::Round($_.Length / 1GB, 2) }
}
@{ model = "Wan2.2-VACE-Fun-A14B"; license = "Apache-2.0 (https://huggingface.co/alibaba-pai/Wan2.2-VACE-Fun-A14B)";
   gguf = "https://huggingface.co/QuantStack/Wan2.2-VACE-Fun-A14B-GGUF"; lora = "https://huggingface.co/lightx2v/Wan2.2-Lightning (Apache-2.0)";
   text_encoder = "$MediaRoot\wan22-ti2v-5b\umt5-xxl-encoder-Q8_0.gguf (shared)"; installed = (Get-Date -Format s); files = $man } |
    ConvertTo-Json -Depth 4 | Set-Content (Join-Path $dest "MANIFEST-local.json")
Write-Host "[vace-fun] OK"; Get-Content (Join-Path $dest "MANIFEST-local.json")
