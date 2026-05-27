#!/bin/bash
# LeadRescuePro Tunnel Watcher - keeps the tunnel alive 24/7
# Runs in background, auto-restarts if tunnel dies

PID_FILE="/tmp/lrp_tunnel.pid"
URL_FILE="$HOME/.lrp_voice_url.txt"
VOICE_BRIDGE_PORT=8643
CLOUDFLARED="$HOME/.npm/_npx/8a26fc3a61fe4212/node_modules/cloudflared/bin/cloudflared"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

cleanup() {
  if [ -f "$PID_FILE" ]; then
    kill $(cat "$PID_FILE") 2>/dev/null
    rm -f "$PID_FILE"
  fi
  log "Tunnel stopped"
}

start_tunnel() {
  cleanup
  
  # First try localtunnel (more reliable)
  URL=$(npx localtunnel --port $VOICE_BRIDGE_PORT 2>&1 | grep -o 'https://[a-zA-Z0-9-]*\.loca\.lt' | head -1)
  
  if [ -n "$URL" ]; then
    echo "$URL" > "$URL_FILE"
    log "Tunnel URL: $URL"
    return 0
  fi
  
  # Fallback to cloudflared
  URL=$($CLOUDFLARED tunnel --url http://127.0.0.1:$VOICE_BRIDGE_PORT 2>&1 | grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' | head -1)
  
  if [ -n "$URL" ]; then
    echo "$URL" > "$URL_FILE"
    log "Tunnel URL: $URL"
    return 0
  fi
  
  log "Failed to start any tunnel"
  return 1
}

# Main loop
log "Tunnel watcher started"
while true; do
  start_tunnel
  if [ $? -eq 0 ]; then
    URL=$(cat "$URL_FILE")
    log "Tunnel is live at: $URL"
    # Wait and check if tunnel is still alive
    sleep 60
    # Check if tunnel process is still running
    if [ -f "$PID_FILE" ] && ! kill -0 $(cat "$PID_FILE") 2>/dev/null; then
      log "Tunnel died, restarting..."
    fi
  else
    log "Tunnel failed, retrying in 10s..."
    sleep 10
  fi
done
