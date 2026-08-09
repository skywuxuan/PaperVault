$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$modelRoot = Join-Path $PSScriptRoot "data\models"
$modelDirectory = Join-Path $modelRoot "translate-en_zh-1_9"
$modelFile = Join-Path $modelDirectory "model\model.bin"
$tokenizerFile = Join-Path $modelDirectory "sentencepiece.model"

if ((Test-Path $modelFile) -and (Test-Path $tokenizerFile)) {
    Write-Host "[PaperVault] Offline English-Chinese model is ready."
    exit 0
}

$archive = Join-Path $modelRoot "translate-en_zh-1_9.argosmodel"
$modelUrl = "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel"
$expectedHash = "433E7C4F034D87FBE2353161E05F18646D7999452F801A4E1F0378522B9850AB"
New-Item -ItemType Directory -Path $modelRoot -Force | Out-Null

Write-Host "[PaperVault] Downloading the 70 MB offline English-Chinese model..."
& curl.exe -fL --retry 5 --retry-delay 3 --connect-timeout 30 --max-time 900 --output $archive $modelUrl
if ($LASTEXITCODE -ne 0) {
    throw "Offline translation model download failed."
}

$actualHash = (Get-FileHash $archive -Algorithm SHA256).Hash
if ($actualHash -ne $expectedHash) {
    Remove-Item -LiteralPath $archive -Force
    throw "Offline translation model checksum verification failed."
}

tar -xf $archive -C $modelRoot
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $modelFile) -or -not (Test-Path $tokenizerFile)) {
    throw "Offline translation model extraction failed."
}
Remove-Item -LiteralPath $archive -Force
Write-Host "[PaperVault] Offline English-Chinese model installed."
