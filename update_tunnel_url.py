#!/usr/bin/env python3
"""Update the GitHub Pages docs/ with the current tunnel URL.

Reads from current_tunnel_url.txt (source of truth), updates docs/, and
verifies the tunnel is reachable. Falls back to checking running tunnels
if the file is stale.
"""
import json, os, time, re, urllib.request, subprocess
from pathlib import Path

docs_dir = Path(__file__).parent / "docs"
url_file = Path("/home/hermeseassistant/leadrescuepro_ops/current_tunnel_url.txt")
status_file = Path("/home/hermeseassistant/leadrescuepro_ops/tunnel_status.json")

def find_running_tunnels():
    """Discover running tunnels from localtunnel/cloudflared processes."""
    tunnels = []
    try:
        # Check for localtunnel subdomains
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split("\n"):
            m = re.search(r"lt[^\n]*--subdomain\s+(\S+)", line)
            if m:
                tunnels.append(f"https://{m.group(1)}.loca.lt")
            # Also check lt processes with --port
            m2 = re.search(r"lt[^\n]*--port\s+(\d+)", line)
            if m2:
                tunnels.append(f"https://lrp-dash.loca.lt")
            # Check cloudflared tunnels — extract from trycloudflare.com URLs in log
            m3 = re.search(r"https://([a-z-]+)\.trycloudflare\.com", line)
            if m3 and "cloudflared" in line:
                tunnels.append(f"https://{m3.group(1)}.trycloudflare.com")
    except Exception:
        pass
    # Also try reading cloudflared log directly
    cf_log = Path("/tmp/cf_tunnel.log")
    if cf_log.exists():
        try:
            log_text = cf_log.read_text()
            for m in re.finditer(r"https://([a-z-]+)\.trycloudflare\.com", log_text):
                tunnels.append(f"https://{m.group(1)}.trycloudflare.com")
        except Exception:
            pass
    return list(set(tunnels))

def health_check(url):
    """Check if a tunnel URL is reachable."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/")
        req.add_header("bypass-tunnel-reminder", "true")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False

# 1. Read current tunnel URL from file
current_url = None
if url_file.exists():
    current_url = url_file.read_text().strip()

# 2. If the stored URL is from cloudflared (random subdomain), it's likely stale.
#    Try to find the real running tunnels instead.
if not current_url or "trycloudflare" in (current_url or ""):
    tunnels = find_running_tunnels()
    if tunnels:
        # Use first one that passes health check
        for t in tunnels:
            if health_check(t):
                current_url = t
                break
        else:
            # None passed health check, use the first found
            current_url = tunnels[0]

# 3. Final fallback
if not current_url:
    current_url = "https://lrp-dash.loca.lt"

# 4. Write to docs/ for GitHub Pages
docs_dir.mkdir(parents=True, exist_ok=True)
docs_url_file = docs_dir / "tunnel_url.txt"
docs_url_file.write_text(current_url + "\n")

# 5. Also update docs/index.html
index_html = docs_dir / "index.html"
if index_html.exists():
    content = index_html.read_text()
    new_content = re.sub(
        r'<span id="tunnel-url">[^<]+</span>',
        f'<span id="tunnel-url">{current_url}</span>',
        content
    )
    new_content = re.sub(
        r'Last updated:[^<]*',
        f'Last updated: {time.strftime("%Y-%m-%dT%H:%M")}',
        new_content
    )
    index_html.write_text(new_content)

# 6. Health check
ok = health_check(current_url)

with open(status_file, "w") as f:
    json.dump({"state": "ok" if ok else "error", "url": current_url, "time": time.time()}, f)

print(f"Tunnel URL updated to: {current_url}")
print(f"Health check: {'PASS' if ok else 'FAIL'}")
if not ok:
    print(f"Note: Tunnel is not responding. The backend may need restart.")
