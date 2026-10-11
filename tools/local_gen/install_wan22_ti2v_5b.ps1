# install_wan22_ti2v_5b.ps1 - BACKUP candidate: Wan2.2-TI2V-5B (Apache-2.0) GGUF Q8_0, 720p T2V/I2V, 24 fps.
# All three files were already downloaded on 21.09.2026 (models\media\wan22-ti2v-5b); this script verifies them,
# downloads only what is missing (<= 12 GB worst case) and writes a local manifest. CPU/network only.
param([string]$MediaRoot = "C:\Users\asd\Bossman\models\media")
$ErrorActionPreference = "Stop"
$dest = Join-Path $MediaRoot "wan22-ti2v-5b"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$hf = (Get-Command hf.exe -ErrorAction SilentlyContinue).Source
if (-not $hf) { $hf = (Get-Command huggingface-cli.exe).Source }
$need = @(
    @{ name = "Wan2.2-TI2V-5B-Q8_0.gguf"; minGB = 4.9; repo = "QuantStack/Wan2.2-TI2V-5B-GGUF"; path = "Wan2.2-TI2V-5B-Q8_0.gguf" },
    @{ name = "wan2.2_vae.safetensors"; minGB = 1.2; repo = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"; path = "split_files/vae/wan2.2_vae.safetensors" },
    @{ name = "umt5-xxl-encoder-Q8_0.gguf"; minGB = 5.5; repo = "city96/umt5-xxl-encoder-gguf"; path = "umt5-xxl-encoder-Q8_0.gguf" }
)
foreach ($f in $need) {
    $p = Join-Path $dest $f.name
    if ((Test-Path $p) -and ((Get-Item $p).Length / 1GB -ge $f.minGB)) { Write-Host "[ti2v-5b] present $($f.name) $([math]::Round((Get-Item $p).Length/1GB,2)) GB"; continue }
    Write-Host "[ti2v-5b] downloading $($f.repo)/$($f.path)"
    & $hf download $f.repo $f.path --local-dir (Join-Path $dest "_hf")
    if ($LASTEXITCODE -ne 0) { throw "hf download failed: $($f.repo)" }
    Move-Item (Join-Path $dest ("_hf\" + $f.path)) $p -Force
}
if (Test-Path (Join-Path $dest "_hf")) { Remove-Item (Join-Path $dest "_hf") -Recurse -Force }
$man = @{}
Get-ChildItem $dest -File | Where-Object { $_.Extension -in ".gguf", ".safetensors" } | ForEach-Object { $man[$_.Name] = @{ bytes = $_.Length; gb = [math]::Round($_.Length / 1GB, 2) } }
@{ model = "Wan2.2-TI2V-5B"; license = "Apache-2.0 (https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B)"; installed = (Get-Date -Format s); files = $man } |
    ConvertTo-Json -Depth 4 | Set-Content (Join-Path $dest "MANIFEST-local.json")
Write-Host "[ti2v-5b] OK"; Get-Content (Join-Path $dest "MANIFEST-local.json")
