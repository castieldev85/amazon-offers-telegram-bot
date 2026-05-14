Set-Location -Path $PSScriptRoot
python -m venv venv
. .\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Write-Host "Installazione completata. Ora copia .env.example in .env e avvia con: python main.py" -ForegroundColor Green
