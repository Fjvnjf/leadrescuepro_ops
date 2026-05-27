"""LeadRescuePro Tunnel Guardian
Watches the tunnel, restarts if dead, writes current URL to file.
"""
import subprocess, time, os, sys, signal

TUNNEL_PORT = 8643
TUNNEL_SUBDOMAIN = "lrp-rt"
URL_FILE = os.path.expanduser("~/leadrescuepro_ops/current_tunnel_url.txt")
LOG_FILE = os.path.expanduser("~/leadrescuepro_ops/guardian.log")

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
    print(f"[Guardian] {msg}", flush=True)

def write_url(url):
    with open(URL_FILE, "w") as f:
        f.write(url.strip() + "\n")

def is_tunnel_alive():
    """Check if localtunnel is still running and responding"""
    import http.client
    try:
        conn = http.client.HTTPConnection("127.0.0.1", TUNNEL_PORT, timeout=5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        ok = resp.status == 200
        conn.close()
        if not ok:
            log(f"Tunnel health check returned {resp.status}")
        return ok
    except Exception as e:
        log(f"Tunnel health check failed: {e}")
        return False

def start_tunnel():
    """Start localtunnel and write URL"""
    global tunnel_proc
    log(f"Starting localtunnel on port {TUNNEL_PORT}, subdomain={TUNNEL_SUBDOMAIN}")
    tunnel_proc = subprocess.Popen(
        ["lt", "--port", str(TUNNEL_PORT), "--subdomain", TUNNEL_SUBDOMAIN],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    # Write the URL immediately (fixed subdomain)
    url = f"https://{TUNNEL_SUBDOMAIN}.loca.lt"
    write_url(url)
    log(f"Tunnel started (PID {tunnel_proc.pid}), URL={url}")
    return tunnel_proc

tunnel_proc = None
restart_count = 0

# Kill any old lt processes from this guardian
script_pid = os.getpid()
my_ppid = os.getppid()
for line in os.popen("ps aux | grep 'lt --port'").readlines():
    parts = line.split()
    try:
        # ps aux output: USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND
        pid = int(parts[1])
    except (ValueError, IndexError):
        continue
    try:
        with open(f"/proc/{pid}/status") as f:
            status = f.read()
        ppid_match = [l for l in status.splitlines() if l.startswith("PPid:")]
        if ppid_match:
            ppid = int(ppid_match[0].split(":")[1].strip())
            if ppid == script_pid or ppid == my_ppid:
                continue  # Don't kill our own child
        os.kill(pid, signal.SIGKILL)
        log(f"Killed old lt process {pid}")
    except:
        pass

# Start tunnel
tunnel_proc = start_tunnel()

while True:
    time.sleep(60)  # Check every 60 seconds
    
    # Check if bridge is alive
    if not is_tunnel_alive():
        log("Voice bridge not responding! Restarting...")
        # Try restarting voice bridge
        subprocess.run(["pkill", "-f", "voice_bridge_v2"], capture_output=True)
        time.sleep(2)
        subprocess.Popen(
            ["python3", "-m", "uvicorn", "voice_bridge_v2:app",
             "--host", "0.0.0.0", "--port", str(TUNNEL_PORT)],
            cwd=os.path.expanduser("~/leadrescuepro_ops"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(5)
        continue
    
    # Check if tunnel process is alive
    if tunnel_proc and tunnel_proc.poll() is not None:
        log(f"Tunnel died (exit code {tunnel_proc.returncode}), restarting...")
        restart_count += 1
        if restart_count > 10:
            log("Too many restarts, quitting guardian")
            sys.exit(1)
        tunnel_proc = start_tunnel()
        continue
    
    # Periodically re-assert the URL file
    # Only rewrite if it's been > 5 minutes
    try:
        mtime = os.path.getmtime(URL_FILE)
        if time.time() - mtime > 300:
            write_url(f"https://{TUNNEL_SUBDOMAIN}.loca.lt")
    except:
        write_url(f"https://{TUNNEL_SUBDOMAIN}.loca.lt")
