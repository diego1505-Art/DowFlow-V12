$ErrorActionPreference = "Stop"

python -m PyInstaller --noconfirm --clean --onedir --name DowFlowServer --distpath build-output --workpath build-work app.py
if ($LASTEXITCODE -ne 0) { throw "Le build PyInstaller a échoué." }

npm run electron:dist
if ($LASTEXITCODE -ne 0) { throw "Le build Electron a échoué." }