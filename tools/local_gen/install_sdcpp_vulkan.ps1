# install_sdcpp_vulkan.ps1 - isolated stable-diffusion.cpp Vulkan build for the local-gen candidates.
# Does NOT touch media-runtime\sdcpp\vulkan (the production sd-cli used by tools/genjutsu/film.py).
# Why a second build: the installed sd-cli is commit 74988b2 (21.09.2026); the GGUF 5-D conv3d fix for the
# Wan2.2 / Wan2.1-14B GGUF family landed 27.09.2026 (leejet/stable-diffusion.cpp PR #2062, merge 168f7b8) and the
# Wan2.2 auto-fit segmentation regression was fixed in master-899 (issue #1976). CPU/network only, no GPU use.
param(
    [string]$Tag = "master-956-1b0ba10",
    [string]$Commit = "1b0ba10",
    [string]$MediaRoot = "C:\Users\asd\Bossman\models\media",
    [string]$Flavor = "vulkan"   # vulkan | rocm-10.1.0 (ROCm zip is an optional A/B only; gfx1151 support unverified)
)
$ErrorActionPreference = "Stop"
$dest = Join-Path $MediaRoot ("sdcpp-" + $Flavor + "-" + $Tag)
$zipName = "sd-master-$Commit-bin-win-$Flavor-x64.zip"
$url = "https://github.com/leejet/stable-diffusion.cpp/releases/download/$Tag/$zipName"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$zip = Join-Path $dest $zipName
if (-not (Test-Path (Join-Path $dest "sd-cli.exe"))) {
    Write-Host "[sdcpp] downloading $url"
    & curl.exe -L --fail --retry 3 -o $zip $url
    if ($LASTEXITCODE -ne 0) { throw "download failed: $url" }
    Expand-Archive -Path $zip -DestinationPath $dest -Force
    # some archives nest a folder; flatten if needed
    $nested = Get-ChildItem $dest -Directory | Where-Object { Test-Path (Join-Path $_.FullName "sd-cli.exe") } | Select-Object -First 1
    if ($nested) { Get-ChildItem $nested.FullName | Move-Item -Destination $dest -Force; Remove-Item $nested.FullName -Recurse -Force }
    Remove-Item $zip -Force
}
$cli = Join-Path $dest "sd-cli.exe"
if (-not (Test-Path $cli)) { throw "sd-cli.exe missing in $dest" }
# --help never touches the GPU; it is the only check allowed before the owner reboots.
$ver = (& $cli --help 2>&1 | Select-Object -First 1)
"$ver`ntag=$Tag`nurl=$url`ninstalled=$(Get-Date -Format s)" | Set-Content (Join-Path $dest "VERSION.txt")
Write-Host "[sdcpp] OK $dest :: $ver"
Write-Host "[sdcpp] production sd-cli untouched: C:\Users\asd\Bossman\media-runtime\sdcpp\vulkan\sd-cli.exe"
