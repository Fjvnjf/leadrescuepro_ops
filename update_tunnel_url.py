#!/usr/bin/env python3
"""
Update the GitHub Pages site with the current tunnel URL if it has changed.

This script:
1. Reads the current tunnel URL from environment variable or config
2. Checks the published URL on the GitHub Pages site
3. If they differ, updates the site and commits/pushes
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# Configuration
REPO_DIR = Path("/home/hermeseassistant/leadrescuepro_ops")
URL_SOURCE = "/home/hermeseassistant/leadrescuepro_ops/current_tunnel_url.txt"
TUNNEL_URL_FILE = REPO_DIR / "current_tunnel_url.txt"
SITE_INDEX = REPO_DIR / "docs" / "index.html"
SITE_URL_FILE = REPO_DIR / "docs" / "tunnel_url.txt"

# GitHub Pages URL to check
PUBLISHED_URL = "https://leadrescuepro.github.io/leadrescuepro_ops/tunnel_url.txt"


def run_cmd(cmd, cwd=None, check=True, capture=True):
    """Run a shell command and return the result."""
    cwd = cwd or str(REPO_DIR)
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check and result.returncode != 0:
            print(f"Command failed: {' '.join(cmd)}")
            print(f"stderr: {result.stderr}")
            return None
        return result
    except subprocess.TimeoutExpired:
        print(f"Command timed out: {' '.join(cmd)}")
        return None
    except Exception as e:
        print(f"Error running command: {e}")
        return None


def get_current_tunnel_url():
    """Read the current tunnel URL from the source file."""
    url_file = Path(URL_SOURCE)
    if not url_file.exists():
        print(f"Tunnel URL source file not found: {URL_SOURCE}")
        return None

    url = url_file.read_text().strip()
    if not url:
        print("Tunnel URL source file is empty")
        return None

    # Clean the URL
    url = url.strip().strip("'\"").strip()
    if not url.startswith("http"):
        url = "https://" + url

    # Remove trailing path elements beyond the domain
    # Keep only the scheme + host (with optional port)
    match = re.match(r"(https?://[^/]+)", url)
    if match:
        url = match.group(1)

    print(f"Current tunnel URL: {url}")
    return url


def get_published_url():
    """Fetch the currently published URL from GitHub Pages."""
    try:
        req = urllib.request.Request(
            PUBLISHED_URL,
            headers={"User-Agent": "tunnel-updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8").strip()
            print(f"Published URL: {content}")
            return content
    except urllib.error.HTTPError as e:
        print(f"HTTP error fetching published URL: {e.code} {e.reason}")
        return None
    except urllib.error.URLError as e:
        print(f"URL error fetching published URL: {e.reason}")
        return None
    except Exception as e:
        print(f"Error fetching published URL: {e}")
        return None


def update_site_file(new_url):
    """Update the local site files with the new URL."""
    # Update tunnel_url.txt in docs/
    SITE_URL_FILE.parent.mkdir(parents=True, exist_ok=True)
    SITE_URL_FILE.write_text(new_url + "\n")
    print(f"Updated {SITE_URL_FILE}")

    # Also update the local copy
    TUNNEL_URL_FILE.write_text(new_url + "\n")
    print(f"Updated {TUNNEL_URL_FILE}")

    # Update index.html if it exists
    if SITE_INDEX.exists():
        content = SITE_INDEX.read_text()
        # Replace the tunnel URL in the HTML
        pattern = r'(id="tunnel-url"[^>]*>)[^<]+(<)'
        replacement = rf'\1{new_url}\2'
        new_content = re.sub(pattern, replacement, content, count=1)
        if new_content != content:
            SITE_INDEX.write_text(new_content)
            print(f"Updated {SITE_INDEX}")
        else:
            print("No tunnel URL update needed in index.html")
    else:
        print(f"Site index not found at {SITE_INDEX}, creating basic page")
        html = f"""<!DOCTYPE html>
<html>
<head><title>LeadRescuePro Tunnel</title></head>
<body>
<h1>LeadRescuePro Tunnel</h1>
<p>Current tunnel URL: <span id="tunnel-url">{new_url}</span></p>
<p>Last updated: {__import__('datetime').datetime.now().isoformat()}</p>
</body>
</html>"""
        SITE_INDEX.parent.mkdir(parents=True, exist_ok=True)
        SITE_INDEX.write_text(html)
        print(f"Created {SITE_INDEX}")

    return True


def commit_and_push():
    """Commit and push changes to GitHub."""
    # Check if there are changes
    result = run_cmd(["git", "status", "--porcelain"])
    if result is None:
        return False

    if not result.stdout.strip():
        print("No changes to commit")
        return False

    print("Changes detected:")
    print(result.stdout)

    # Stage changes
    run_cmd(["git", "add", "-A"])

    # Commit
    run_cmd(["git", "commit", "-m", "Update tunnel URL [auto]"])

    # Push
    push_result = run_cmd(["git", "push"], check=False)
    if push_result is None or push_result.returncode != 0:
        print("Push failed (might be permission issue)")
        return False

    print("Changes pushed successfully")
    return True


def main():
    print("=" * 60)
    print("Tunnel URL Updater")
    print("=" * 60)

    # Check if the repo directory exists
    if not REPO_DIR.exists():
        print(f"Repo directory not found: {REPO_DIR}")
        print("Cannot update tunnel URL — repo not available")
        return

    # Check if we're in a git repo
    git_check = run_cmd(["git", "rev-parse", "--git-dir"], check=False)
    if git_check is None or git_check.returncode != 0:
        print("Not a git repository — cannot push updates")
        return

    # Get the current tunnel URL
    current_url = get_current_tunnel_url()
    if not current_url:
        print("No tunnel URL available — skipping update")
        return

    # Get the published URL
    published_url = get_published_url()

    if published_url == current_url:
        print(f"URLs match ({current_url}) — no update needed")
        return

    print(f"URL changed: '{published_url}' -> '{current_url}'")
    print("Updating site files...")

    update_site_file(current_url)

    print("Committing and pushing...")
    commit_and_push()

    print("Done!")


if __name__ == "__main__":
    main()
