Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== LibrarianAI v3 Setup ===" -ForegroundColor Cyan

# Create data directories
New-Item -ItemType Directory -Force -Path "data\covers","data\text_cache","data\debug" | Out-Null

# Python virtual environment
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
.\.venv\Scripts\Activate.ps1

# Python dependencies
pip install -r requirements.txt

# DB migrations
python scripts\migrate_db.py

# Frontend dependencies
Push-Location frontend
npm install
Pop-Location

Write-Host "=== Setup complete. Run: .\run.ps1 ===" -ForegroundColor Green
