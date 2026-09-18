$ErrorActionPreference = "Stop"

$BASE_DIR = "data"

$DIRS = @(
    "training/detection/BDD100K",
    "training/detection/MIO-TCD",
    "training/detection/VisDrone",
    "training/tracking/MOT17",
    "training/tracking/BDD100K-MOT",
    "training/activity/VIRAT",
    "training/activity/MEVA",
    "training/activity/UCF-Crime",
    "training/face/WIDER-FACE",
    "training/anpr/CCPD",
    "training/anpr/UFPR-ALPR",
    "testing/mot17_holdout",
    "testing/street_scene",
    "testing/virat_holdout",
    "testing/night",
    "testing/cctv",
    "testing/real_world",
    "demo"
)

Write-Host "Creating Prahari Dataset Directory Structure..." -ForegroundColor Cyan

foreach ($dir in $DIRS) {
    $target = Join-Path $BASE_DIR $dir
    if (!(Test-Path $target)) {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Write-Host "Created: $target"
    } else {
        Write-Host "Exists: $target" -ForegroundColor DarkGray
    }
}

Write-Host "`nDirectory structure successfully initialized.`n" -ForegroundColor Green

Write-Host "Downloading MOT17 sample benchmark zip..."
$mot17Zip = "$BASE_DIR/training/tracking/MOT17.zip"
Invoke-WebRequest -Uri "https://motchallenge.net/data/MOT17.zip" -OutFile $mot17Zip
Write-Host "Done downloading MOT17. Note: full extraction requires 5.5GB." -ForegroundColor Green

# Record the checksum so provenance in DATA_LICENSES.md can be verified/updated.
if (Test-Path $mot17Zip) {
    $hash = (Get-FileHash -Algorithm SHA256 $mot17Zip).Hash
    Write-Host "MOT17.zip SHA-256: $hash" -ForegroundColor Cyan
    Write-Host "Paste this into the MOT17 'Checksum' field in DATA_LICENSES.md." -ForegroundColor DarkGray
}

Write-Host "`nFor the remaining datasets, please refer to docs/data-strategy.md for registration and license agreements prior to downloading." -ForegroundColor Yellow
Write-Host "Record provenance (source, date, license, checksum) for each in DATA_LICENSES.md." -ForegroundColor Yellow
