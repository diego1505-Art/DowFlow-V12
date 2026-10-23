import atexit
import re
import shutil
import subprocess
import threading
import uuid
from typing import Any


class TunnelError(RuntimeError):
    pass


_LOCK = threading.RLock()
_PROCESS: subprocess.Popen[str] | None = None
_PROVIDER = ""
_SCOPE = "root"
_BASE_URL = ""
_TOKEN = ""
_LAST_ERROR = ""


def _read_output(process: subprocess.Popen[str], token: str) -> None:
    global _BASE_URL, _LAST_ERROR
    if process.stdout is None:
        return
    for line in process.stdout:
        urls = re.findall(r"https://[^\s\"']+", line)
        with _LOCK:
            if token != _TOKEN:
                return
            if urls and not _BASE_URL:
                _BASE_URL = urls[0].rstrip(".,)")
            if "error" in line.lower() or "failed" in line.lower():
                _LAST_ERROR = line.strip()[-500:]


def _is_running() -> bool:
    return _PROCESS is not None and _PROCESS.poll() is None


def status() -> dict[str, Any]:
    with _LOCK:
        running = _is_running()
        url = f"{_BASE_URL}/{_SCOPE.lstrip('/')}" if _BASE_URL else ""
        return {
            "running": running,
            "provider": _PROVIDER,
            "scope": _SCOPE,
            "url": url,
            "ready": bool(url),
            "error": _LAST_ERROR,
        }


def start(provider: str, scope: str) -> dict[str, Any]:
    global _PROCESS, _PROVIDER, _SCOPE, _BASE_URL, _TOKEN, _LAST_ERROR
    provider = provider.strip().lower()
    if provider not in {"cloudflare", "ngrok"}:
        raise TunnelError("Ce fournisseur ne peut pas être démarré automatiquement.")
    executable = shutil.which("cloudflared" if provider == "cloudflare" else "ngrok")
    if not executable:
        command = "cloudflared" if provider == "cloudflare" else "ngrok"
        raise TunnelError(f"{command} est introuvable dans le PATH.")

    with _LOCK:
        if _is_running() and _PROVIDER == provider:
            return status()
        if _is_running():
            stop()
        args = (
            [executable, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:5001"]
            if provider == "cloudflare"
            else [executable, "http", "5001", "--log=stdout"]
        )
        try:
            _PROCESS = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise TunnelError(f"Impossible de démarrer {provider} : {error}") from error
        _PROVIDER = provider
        _SCOPE = scope
        _BASE_URL = ""
        _LAST_ERROR = ""
        _TOKEN = uuid.uuid4().hex
        threading.Thread(target=_read_output, args=(_PROCESS, _TOKEN), daemon=True).start()
        return status()


def stop() -> dict[str, Any]:
    global _PROCESS, _PROVIDER, _SCOPE, _BASE_URL, _TOKEN, _LAST_ERROR
    with _LOCK:
        process = _PROCESS
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        _PROCESS = None
        _PROVIDER = ""
        _SCOPE = "root"
        _BASE_URL = ""
        _TOKEN = ""
        _LAST_ERROR = ""
        return status()


atexit.register(stop)