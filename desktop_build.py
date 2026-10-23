import subprocess
import threading
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
_build_process: subprocess.Popen[str] | None = None
_build_output = ""
_build_error = ""
_lock = threading.RLock()


def _read_output(process: subprocess.Popen[str]) -> None:
    global _build_output, _build_error
    if process.stdout is None:
        return
    for line in process.stdout:
        with _lock:
            _build_output = (_build_output + line)[-4000:]
            if "error" in line.lower() or "failed" in line.lower():
                _build_error = line.strip()[-500:]


def start() -> dict[str, Any]:
    global _build_process, _build_output, _build_error
    with _lock:
        if _build_process is not None and _build_process.poll() is None:
            return status()
        script = ROOT / "build-desktop.ps1"
        if not script.exists():
            raise RuntimeError("Le script de build Windows est introuvable.")
        _build_output = ""
        _build_error = ""
        _build_process = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=_read_output, args=(_build_process,), daemon=True).start()
        return status()


def status() -> dict[str, Any]:
    with _lock:
        running = _build_process is not None and _build_process.poll() is None
        installers = sorted((ROOT / "dist").glob("*.exe")) if (ROOT / "dist").exists() else []
        return {
            "running": running,
            "success": bool(installers) and not running,
            "error": _build_error,
            "output": _build_output,
            "download": f"/api/publisher/build/windows/download/{installers[-1].name}" if installers else "",
        }
