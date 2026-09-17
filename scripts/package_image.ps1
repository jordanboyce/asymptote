<#
.SYNOPSIS
    Build a portable Clio image and package it for a machine that cannot
    build one (air gap, customer site, appliance). Windows counterpart of
    scripts/package_image.sh — same output, same instructions.

.EXAMPLE
    .\scripts\package_image.ps1
    .\scripts\package_image.ps1 -OfflineBundle -Tag 1.4.0 -Out D:\transfer
#>
[CmdletBinding()]
param(
    [string]$Tag = "local",
    [string]$Out = "dist-image",
    [switch]$OfflineBundle,
    [switch]$WithDocling,
    [string]$WhisperModel = "base",
    # Build for the target's architecture when it differs from this machine's.
    [string]$Platform = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$image = "clio:$Tag"
$vcsRef = "unknown"
try { $vcsRef = (git rev-parse --short HEAD).Trim() } catch { }

$offline = "0"; if ($OfflineBundle) { $offline = "1" }
$docling = "0"; if ($WithDocling) { $docling = "1" }

Write-Host "==> Building $image (offline_bundle=$offline, docling=$docling)"
$buildArgs = @(
    "build",
    "--build-arg", "OFFLINE_BUNDLE=$offline",
    "--build-arg", "WITH_DOCLING=$docling",
    "--build-arg", "WHISPER_MODEL=$WhisperModel",
    "--build-arg", "APP_VERSION=$Tag",
    "--build-arg", "VCS_REF=$vcsRef"
)
if ($Platform) { $buildArgs += @("--platform", $Platform) }
$buildArgs += @("-t", $image, ".")
& docker @buildArgs
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

if (-not (Test-Path $Out)) { New-Item -ItemType Directory -Path $Out | Out-Null }
$tar = Join-Path $Out "clio-$Tag.tar"
$archive = "$tar.gz"

# `docker save -o` rather than a pipeline: PowerShell pipes text, and would
# corrupt the tar stream on its way to a file.
Write-Host "==> Saving $image"
& docker save -o $tar $image
if ($LASTEXITCODE -ne 0) { throw "docker save failed" }

Write-Host "==> Compressing"
$in = [System.IO.File]::OpenRead($tar)
$outStream = [System.IO.File]::Create($archive)
$gzip = New-Object System.IO.Compression.GZipStream($outStream, [System.IO.Compression.CompressionMode]::Compress)
try { $in.CopyTo($gzip) } finally { $gzip.Dispose(); $outStream.Dispose(); $in.Dispose() }
Remove-Item $tar

Write-Host "==> Checksumming"
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
# sha256sum format, so the receiving Linux host can verify with `sha256sum -c`.
"$hash  clio-$Tag.tar.gz" | Out-File -FilePath "$archive.sha256" -Encoding ascii

Write-Host "==> Copying deployment files"
Copy-Item docker-compose.onprem.yml, .env.onprem.example, docs\ONPREM.md, docs\AIRGAP.md $Out

$built = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
@"
Clio $Tag - offline install bundle
built $built from $vcsRef
offline model bundle: $offline   docling OCR: $docling

On the target machine:

  sha256sum -c clio-$Tag.tar.gz.sha256
  docker load < clio-$Tag.tar.gz
  cp .env.onprem.example .env      # then edit: model endpoint, AUTH_PASSWORD
  # set CLIO_IMAGE=clio:$Tag in .env
  docker compose -f docker-compose.onprem.yml up -d

Then open http://<host>:8473. Full guide: ONPREM.md (air gap: AIRGAP.md).
"@ | Out-File -FilePath (Join-Path $Out "README.txt") -Encoding utf8

Write-Host ""
Write-Host "Bundle ready in ${Out}:"
Get-ChildItem $Out | Select-Object Name, Length
