"""
LeadRescuePro Voice Bridge — connects voice interface to Hermes API.
Run: python3 voice_bridge.py
Then open http://YOUR_IP:8643 in your browser.
"""
import json
import http.server
import urllib.request
import urllib.error
import os
import signal
import sys

HERMES_API = "http://127.0.0.1:8642/v1/chat/completions"
PORT = 8643

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>LeadRescuePro Voice Assistant</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0e17;
    color: #c8d6e5;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }
  .container {
    max-width: 480px;
    width: 100%;
    text-align: center;
  }
  .header {
    margin-bottom: 32px;
  }
  .header h1 {
    font-size: 20px;
    font-weight: 700;
    color: #00d4ff;
    text-shadow: 0 0 20px rgba(0,212,255,0.15);
    letter-spacing: 1px;
  }
  .header p {
    font-size: 13px;
    color: #5a7a9a;
    margin-top: 4px;
  }
  .status-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
    animation: pulse 1.5s infinite;
  }
  .status-dot.idle { background: #5a7a9a; animation: none; }
  .status-dot.listening { background: #00d4ff; }
  .status-dot.processing { background: #ffa502; }
  .status-dot.speaking { background: #2ed573; }
  .status-dot.error { background: #ff4757; animation: none; }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  .mic-button {
    width: 120px;
    height: 120px;
    border-radius: 50%;
    border: 3px solid #1a2a44;
    background: linear-gradient(135deg, #11192e 0%, #0f1629 100%);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 24px auto;
    transition: all 0.3s ease;
    position: relative;
    box-shadow: 0 0 30px rgba(0,212,255,0.05);
  }
  .mic-button svg { width: 48px; height: 48px; fill: #5a7a9a; transition: fill 0.3s; }
  .mic-button.active {
    border-color: #00d4ff;
    box-shadow: 0 0 40px rgba(0,212,255,0.2);
    animation: breathe 1.5s ease-in-out infinite;
  }
  .mic-button.active svg { fill: #00d4ff; }
  .mic-button.processing {
    border-color: #ffa502;
    box-shadow: 0 0 40px rgba(0,212,255,0.1);
  }
  .mic-button.processing svg { fill: #ffa502; }
  .mic-button.speaking {
    border-color: #2ed573;
    box-shadow: 0 0 30px rgba(46,213,115,0.15);
  }
  .mic-button.speaking svg { fill: #2ed573; }

  @keyframes breathe {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.05); }
  }

  .status-text {
    font-size: 14px;
    color: #5a7a9a;
    margin: 8px 0 24px;
    min-height: 20px;
  }

  .transcript-box {
    background: linear-gradient(135deg, #11192e 0%, #0f1629 100%);
    border: 1px solid #1a2a44;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 12px;
    min-height: 50px;
    text-align: left;
    font-size: 14px;
    line-height: 1.6;
  }
  .transcript-box .label {
    font-size: 11px;
    color: #5a7a9a;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
  }
  .transcript-box .text { color: #c8d6e5; }
  .transcript-box .text.you { color: #00d4ff; }
  .transcript-box .text.hermes { color: #2ed573; }
  .transcript-box .placeholder { color: #3a4a5a; font-style: italic; }

  .controls {
    display: flex;
    gap: 12px;
    justify-content: center;
    margin-top: 24px;
  }
  .controls button {
    padding: 10px 20px;
    border-radius: 8px;
    border: 1px solid #1a2a44;
    background: #11192e;
    color: #5a7a9a;
    font-size: 13px;
    cursor: pointer;
    transition: all 0.2s;
  }
  .controls button:hover { border-color: #2a4a6a; color: #8aabcc; }
  .controls button:disabled { opacity: 0.4; cursor: not-allowed; }

  .voice-selector {
    margin-top: 16px;
    font-size: 12px;
    color: #5a7a9a;
  }
  .voice-selector select {
    background: #11192e;
    border: 1px solid #1a2a44;
    color: #8aabcc;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 12px;
    margin-left: 6px;
  }

  .conversation {
    max-height: 300px;
    overflow-y: auto;
    margin-bottom: 12px;
    scrollbar-width: thin;
    scrollbar-color: #1a2a44 transparent;
  }
  .conversation::-webkit-scrollbar { width: 4px; }
  .conversation::-webkit-scrollbar-thumb { background: #1a2a44; border-radius: 2px; }

  .hint {
    font-size: 12px;
    color: #3a4a5a;
    margin-top: 16px;
  }
  .hint kbd {
    background: #11192e;
    border: 1px solid #1a2a44;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 11px;
  }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>LeadRescuePro</h1>
    <p>Voice Assistant</p>
  </div>

  <div id="status" class="status-text">
    <span class="status-dot idle" id="statusDot"></span>
    <span id="statusLabel">Tap the mic and speak</span>
  </div>

  <button class="mic-button" id="micButton" onclick="toggleMic()">
    <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
      <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
    </svg>
  </button>

  <div class="conversation" id="conversation">
    <div class="transcript-box">
      <div class="label">LeadRescuePro</div>
      <div class="text hermes">Hello! I'm your LeadRescuePro voice assistant. Tap the mic button and speak — I'll listen, think, and respond by voice. Try asking me about your leads, your caller's performance, or anything about the business.</div>
    </div>
  </div>

  <div class="controls">
    <button onclick="clearConversation()">Clear</button>
    <button onclick="reconnect()">Reconnect</button>
  </div>

  <div class="voice-selector">
    Voice:
    <select id="voiceSelect" onchange="preferredVoice = this.value">
      <option value="">Auto</option>
    </select>
  </div>
  <div class="hint">Tap mic → speak → I respond by voice. Press <kbd>Space</kbd> to toggle mic.</div>
</div>

<script>
// ===== State =====
let isListening = false;
let isProcessing = false;
let isSpeaking = false;
let mediaRecorder = null;
let audioChunks = [];
let audioContext = null;
let preferredVoice = '';
let voices = [];

// ===== DOM refs =====
const micButton = document.getElementById('micButton');
const statusDot = document.getElementById('statusDot');
const statusLabel = document.getElementById('statusLabel');
const conversation = document.getElementById('conversation');
const voiceSelect = document.getElementById('voiceSelect');

// ===== Set status =====
function setStatus(state, label) {
  statusDot.className = 'status-dot ' + state;
  micButton.className = 'mic-button';
  statusLabel.textContent = label;
  isListening = state === 'listening';
  isProcessing = state === 'processing';
  isSpeaking = state === 'speaking';
  if (state === 'listening') micButton.classList.add('active');
  else if (state === 'processing') micButton.classList.add('processing');
  else if (state === 'speaking') micButton.classList.add('speaking');
}

// ===== Add message =====
function addMessage(role, text) {
  const div = document.createElement('div');
  div.className = 'transcript-box';
  div.innerHTML = '<div class="label">' + (role === 'user' ? 'You' : 'LeadRescuePro') + '</div>' +
    '<div class="text ' + role + '">' + escapeHtml(text) + '</div>';
  conversation.appendChild(div);
  conversation.scrollTop = conversation.scrollHeight;
}

function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

// ===== Clear conversation =====
function clearConversation() {
  conversation.innerHTML = '';
  addMessage('assistant', 'Conversation cleared. Tap the mic to start again.');
}

// ===== Populate voices =====
function populateVoices() {
  voices = speechSynthesis.getVoices();
  const current = voiceSelect.value;
  voiceSelect.innerHTML = '<option value="">Auto</option>';
  voices.forEach(v => {
    const opt = document.createElement('option');
    opt.value = v.name;
    opt.textContent = v.name + ' (' + v.lang + ')';
    voiceSelect.appendChild(opt);
  });
  voiceSelect.value = current;
}
speechSynthesis.onvoiceschanged = populateVoices;
setTimeout(populateVoices, 500);

// ===== Speak response =====
function speak(text) {
  return new Promise((resolve) => {
    if (!text || text.trim().length === 0) { resolve(); return; }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.volume = 1.0;
    if (preferredVoice) {
      const found = voices.find(v => v.name === preferredVoice);
      if (found) utterance.voice = found;
    }
    setStatus('speaking', 'Speaking...');
    utterance.onend = () => { setStatus('idle', 'Tap the mic and speak'); resolve(); };
    utterance.onerror = () => { setStatus('idle', 'Tap the mic and speak'); resolve(); };
    speechSynthesis.speak(utterance);
  });
}

// ===== Send to Hermes API =====
async function sendToHermes(text) {
  setStatus('processing', 'Thinking...');
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: getSessionId() })
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    return data.response;
  } catch (e) {
    console.error('API error:', e);
    return 'Sorry, I had trouble connecting. Please try again.';
  }
}

// ===== Session ID =====
function getSessionId() {
  let sid = localStorage.getItem('lrp_voice_session');
  if (!sid) {
    sid = 'voice_' + Date.now() + '_' + Math.random().toString(36).slice(2,8);
    localStorage.setItem('lrp_voice_session', sid);
  }
  return sid;
}

// ===== Toggle Microphone =====
function toggleMic() {
  if (isListening) {
    stopRecording();
  } else if (isProcessing || isSpeaking) {
    // Don't interrupt processing or speech
    return;
  } else {
    startListening();
  }
}

// ===== Start listening =====
async function startListening() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
    audioChunks = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      if (audioChunks.length === 0) { setStatus('idle', 'Tap the mic and speak'); return; }
      await processAudio();
    };

    mediaRecorder.start();
    setStatus('listening', 'Listening...');
  } catch (e) {
    console.error('Mic error:', e);
    setStatus('error', 'Microphone access denied. Allow mic permissions in your browser.');
    setTimeout(() => setStatus('idle', 'Tap the mic and speak'), 3000);
  }
}

// ===== Stop recording =====
function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
  }
}

// ===== Process audio =====
async function processAudio() {
  const blob = new Blob(audioChunks, { type: 'audio/webm' });

  setStatus('processing', 'Transcribing...');

  try {
    // Send audio to server for transcription
    const formData = new FormData();
    formData.append('audio', blob, 'recording.webm');

    const transResp = await fetch('/api/transcribe', {
      method: 'POST',
      body: formData
    });
    if (!transResp.ok) throw new Error('Transcription failed');
    const transData = await transResp.json();
    const userText = transData.text;

    if (!userText || userText.trim().length === 0) {
      setStatus('idle', 'Tap the mic and speak');
      return;
    }

    addMessage('user', userText);

    // Send to Hermes
    const hermesResponse = await sendToHermes(userText);
    addMessage('assistant', hermesResponse);

    // Speak response
    await speak(hermesResponse);

  } catch (e) {
    console.error('Audio processing error:', e);
    setStatus('error', 'Something went wrong. Try again.');
    setTimeout(() => setStatus('idle', 'Tap the mic and speak'), 2000);
  }
}

// ===== Keyboard shortcut =====
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  if (e.code === 'Space') {
    e.preventDefault();
    toggleMic();
  }
});

// ===== Reconnect =====
function reconnect() {
  setStatus('idle', 'Reconnected. Tap the mic and speak.');
}

// Initial message
setTimeout(() => {
  setStatus('idle', 'Tap the mic and speak');
}, 500);
</script>
</body>
</html>
"""

class VoiceBridgeHandler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def _send_html(self, html):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return b''
        return self.rfile.read(length)
    
    def log_message(self, format, *args):
        pass  # Keep logs clean
    
    def do_OPTIONS(self):
        self._send_json({})
    
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self._send_html(HTML)
        elif self.path == '/health':
            self._send_json({'status': 'ok', 'service': 'lrp-voice-bridge'})
        else:
            self._send_json({'error': 'Not found'}, 404)
    
    def do_POST(self):
        if self.path == '/api/chat':
            try:
                body = json.loads(self._read_body())
                message = body.get('message', '')
                session_id = body.get('session_id', '')
                
                # Call Hermes API
                hermes_payload = json.dumps({
                    'model': 'hermes-agent',
                    'messages': [
                        {'role': 'system', 'content': 'You are the LeadRescuePro voice assistant. You help Fahim manage his lead generation business for US plumbing companies. You have full access to all his tools, data, and systems. Keep responses conversational and concise since this is a voice conversation. Answer naturally like a human would speak.'},
                        {'role': 'user', 'content': message}
                    ],
                    'stream': False,
                    'max_tokens': 2000
                }).encode()
                
                req = urllib.request.Request(
                    HERMES_API,
                    data=hermes_payload,
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        result = json.loads(resp.read())
                        response_text = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                        if not response_text:
                            response_text = "I'm sorry, I couldn't process that. Could you try again?"
                except urllib.error.HTTPError as e:
                    response_text = f"I'm sorry, I'm having trouble connecting. Error: {e.code}"
                except urllib.error.URLError:
                    response_text = "I'm sorry, the system seems to be busy. Please try again in a moment."
                
                self._send_json({'response': response_text})
            except Exception as e:
                self._send_json({'error': str(e)}, 500)
        
        elif self.path == '/api/transcribe':
            # Simple echo for now — browser does its own speech recognition
            # If the browser doesn't support SpeechRecognition, this would use Whisper
            try:
                body = self._read_body()
                # For now, return empty — we use browser speech-to-text
                self._send_json({'text': ''})
            except Exception as e:
                self._send_json({'error': str(e)}, 500)
        
        else:
            self._send_json({'error': 'Not found'}, 404)


def main():
    server = http.server.HTTPServer(('0.0.0.0', PORT), VoiceBridgeHandler)
    server.timeout = 0.5
    
    def shutdown(sig, frame):
        print('\nShutting down voice bridge...')
        server.shutdown()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    
    print(f'LeadRescuePro Voice Bridge running on http://0.0.0.0:{PORT}')
    print(f'Open this URL in your browser to talk to me by voice!')
    print(f'Press Ctrl+C to stop.')
    
    # Try to detect public IP
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        print(f'\nOn your phone, open: http://{ip}:{PORT}')
    except:
        pass
    
    server.serve_forever()

if __name__ == '__main__':
    main()
