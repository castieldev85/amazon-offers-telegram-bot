Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".env")) {
    Write-Host "Manca il file .env. Copio .env.example in .env..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "Apri .env e inserisci TELEGRAM_BOT_TOKEN e chiavi Amazon, poi rilancia questo script." -ForegroundColor Yellow
    notepad .env
    exit
}

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Creo ambiente virtuale..." -ForegroundColor Cyan
    python -m venv venv
}

. .\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
