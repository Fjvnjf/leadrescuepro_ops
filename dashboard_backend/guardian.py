#!/usr/bin/env python3
"""
LeadRescuePro backend guardian.

Keeps the FastAPI dashboard server alive on port 8650. This intentionally
does not manage public tunnels; the tunnel URL changes and is tracked in
docs/dashboard_tunnel_url.txt by the deploy workflow.
"""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen


BASE_DIR = Path(__file__).resolve().parent
PORT = int(os.environ.get("LRP_PORT", "8650"))
PYTHON = os.environ.get("PYTHON", sys.executable)
CHECK_INTERVAL = int(os.environ.get("LRP_GUARDIAN_INTERVAL", "20"))
LOG_PATH = BASE_DIR / "data" / "guardian.log"


def log(message):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [guardian] {message}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def port_open():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def health_ok():
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def stop_process(proc):
    if not proc or proc.poll() is not None:
        return
    log(f"Stopping backend pid={proc.pid}")
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def start_backend():
    log(f"Starting backend on port {PORT}")
    return subprocess.Popen(
        [PYTHON, "main.py"],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main():
    proc = None

    def handle_signal(signum, _frame):
        log(f"Received signal {signum}; shutting down")
        stop_process(proc)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log("Guardian started")
    while True:
        if proc and proc.poll() is not None:
            log(f"Backend exited with code {proc.returncode}")
            proc = None

        if not port_open() or not health_ok():
            stop_process(proc)
            proc = start_backend()
            time.sleep(5)
            if health_ok():
                log("Backend health check passed")
            else:
                log("Backend health check still failing")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
