#!/usr/bin/env python3
"""
LRP Dashboard Guardian - keeps dashboard & tunnel alive 24/7
Runs as a background process, checks every 60 seconds.
"""
import os, sys, time, json, logging, subprocess, socket
from pathlib import Path

BASE = Path("/home/hermeseassistant/leadrescuepro_ops")
BACKEND_DIR = BASE / "dashboard_backend"
LOG_FILE = BASE / "guardian.log"
STATUS_FILE = BASE / "tunnel_status.json"
PORT = 8650

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GUARDIAN] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger("guardian")

def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

def start_server():
    log.info("Starting backend server...")
    subprocess.Popen(
        ["python", "-c", "import uvicorn; uvicorn.run('main:app', host='0.0.0.0', port=%d)" % PORT],
        cwd=str(BACKEND_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )

def start_tunnel():
    log.info("Starting loca.lt tunnel...")
    subprocess.Popen(
        ["npx", "localtunnel", "--port", str(PORT), "--subdomain", "lrp-dash"],
        cwd=str(BASE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )

def check_api():
    """Quick health check via localhost"""
    try:
        import urllib.request
        r = urllib.request.urlopen("http://127.0.0.1:%d/api/health" % PORT, timeout=5)
        return r.status == 200
    except:
        return False

def save_status(state):
    with open(STATUS_FILE, "w") as f:
        json.dump({"state": state, "time": time.time()}, f)

def main():
    log.info("Guardian started. Monitoring dashboard...")
    
    # Initial start
    if not port_in_use(PORT):
        log.warning("Port %d not in use. Starting server..." % PORT)
        start_server()
        time.sleep(3)
    
    if not check_api():
        log.warning("API not responding. Force restarting...")
        os.system("fuser -k %d/tcp 2>/dev/null" % PORT)
        time.sleep(2)
        start_server()
        time.sleep(3)
    
    start_tunnel()
    save_status("starting")
    
    while True:
        try:
            server_ok = port_in_use(PORT) and check_api()
            if not server_ok:
                log.error("Server DOWN! Restarting...")
                os.system("fuser -k %d/tcp 2>/dev/null" % PORT)
                time.sleep(2)
                start_server()
                time.sleep(3)
                save_status("restarted")
            else:
                save_status("ok")
            
            # Tunnel check - try connecting via localhost:8651 (gives us a quick check)
            time.sleep(45)
        except Exception as e:
            log.error(f"Guardian error: {e}")
            time.sleep(15)

if __name__ == "__main__":
    main()
