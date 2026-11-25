# One-time setup for Jarvis. Safe to re-run.
#   1. uv + Python environment (uses the signed python.org Python 3.14;
#      uv's own Python builds are blocked when Smart App Control is on)
#   2. speech models and the offline city list into models\
#   3. Ollama + the AI model into models\ollama
#   4. the Lively wallpaper zip
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$env:UV_CACHE_DIR = Join-Path $Root '.uv\cache'
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')

function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }

Step 'Settings'
if (-not (Test-Path config.toml)) { Copy-Item config.example.toml config.toml; Write-Host 'Created config.toml from config.example.toml' }

Step 'uv'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    winget install --id astral-sh.uv -e --source winget --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
}

Step 'Python environment'
$py = (py -3.14 -c "import sys; print(sys.executable)" 2>$null)
if (-not $py) { throw 'Python 3.14 from python.org is needed (winget install Python.Python.3.14).' }
uv sync --python $py --no-managed-python --extra google --group dev

Step 'Speech models'
.\.venv\Scripts\python.exe -W ignore scripts\download_models.py

Step 'Ollama'
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS', (Join-Path $Root 'models\ollama'), 'User')
$env:OLLAMA_MODELS = Join-Path $Root 'models\ollama'
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    winget install --id Ollama.Ollama -e --source winget --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
}
ollama pull qwen3.5:4b

Step 'Wallpaper'
.\.venv\Scripts\python.exe -W ignore scripts\make_wallpaper.py

Write-Host "`nDone. Next: run the benchmark (scripts\bench.py), then start Jarvis: .\.venv\Scripts\python.exe -m jarvis" -ForegroundColor Green
