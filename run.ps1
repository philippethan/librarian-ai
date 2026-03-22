# Starts backend in a new PowerShell window, frontend in current terminal.
.\.venv\Scripts\Activate.ps1

$backendCmd = "cd '$PWD'; .\.venv\Scripts\Activate.ps1; " +
              "uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload"

Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd

Push-Location frontend
npm run dev
Pop-Location
