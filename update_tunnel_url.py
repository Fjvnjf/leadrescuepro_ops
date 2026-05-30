#!/usr/bin/env python3
import json, os, time, re, urllib.request
from pathlib import Path

url_file = Path("/home/hermeseassistant/leadrescuepro_ops/current_tunnel_url.txt")
status_file = Path("/home/hermeseassistant/leadrescuepro_ops/tunnel_status.json")

new_url = "https://arg-block-sociology-mercury.trycloudflare.com"
url_file.write_text(new_url + "\n")

# Also update the docs/ copy that GitHub Pages serves
docs_file = Path(__file__).parent / "docs" / "tunnel_url.txt"
docs_file.write_text(new_url + "\n")

# Verify
try:
    req = urllib.request.Request(new_url + "/api/health")
    with urllib.request.urlopen(req, timeout=10) as resp:
        ok = resp.status == 200
except:
    ok = False

with open(status_file, "w") as f:
    json.dump({"state": "ok" if ok else "error", "url": new_url, "time": time.time()}, f)

print(f"Tunnel URL updated to: {new_url}")
print(f"Health check: {'PASS' if ok else 'FAIL'}")
