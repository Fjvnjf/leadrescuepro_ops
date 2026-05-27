/**
 * LeadRescuePro Voice Assistant - Speech handling
 * Uses browser Web Speech API for speech-to-text (Chrome/Safari)
 * Falls back to text input if speech not available
 */

// ===== State =====
let isListening = false;
let isProcessing = false;
let isSpeaking = false;
let mediaRecorder = null;
let audioChunks = [];
let recognition = null;
let preferredVoice = '';
let voices = [];
let speechSupported = false;

// ===== DOM refs =====
const micButton = document.getElementById('micButton');
const statusDot = document.getElementById('statusDot');
const statusLabel = document.getElementById('statusLabel');
const conversation = document.getElementById('conversation');
const voiceSelect = document.getElementById('voiceSelect');

// ===== Check speech support =====
function detectSpeechSupport() {
  speechSupported = 'webkitSpeechRecognition' in window || 'SpeechRecognition' in window;
  if (!speechSupported) {
    // Add text input as fallback
    const container = document.querySelector('.container');
    const inputDiv = document.createElement('div');
    inputDiv.innerHTML = `
      <div style="display:flex; gap:8px; margin-top:12px;">
        <input type="text" id="textInput" placeholder="Type your message..." 
               style="flex:1; padding:12px; background:#11192e; border:1px solid #1a2a44; 
                      border-radius:8px; color:#c8d6e5; font-size:14px;">
        <button onclick="sendTextMessage()" style="padding:12px 20px; background:#1a3a5a; 
                border:1px solid #2a4a6a; border-radius:8px; color:#00d4ff; font-weight:600; cursor:pointer;">
          Send
        </button>
      </div>
    `;
    container.appendChild(inputDiv);
    
    document.getElementById('textInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') sendTextMessage();
    });
  }
}

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
  if (typeof speechSynthesis === 'undefined') return;
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
if (typeof speechSynthesis !== 'undefined') {
  speechSynthesis.onvoiceschanged = populateVoices;
  setTimeout(populateVoices, 500);
}

// ===== Speak response =====
function speak(text) {
  return new Promise((resolve) => {
    if (!text || text.trim().length === 0) { resolve(); return; }
    if (typeof speechSynthesis === 'undefined') { resolve(); return; }
    
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

// ===== Text fallback =====
function sendTextMessage() {
  if (isProcessing || isSpeaking) return;
  const input = document.getElementById('textInput');
  const text = input.value.trim();
  if (!text) return;
  
  input.value = '';
  addMessage('user', text);
  
  processAndSpeak(text);
}

// ===== Process user text (shared path for voice + text) =====
async function processAndSpeak(text) {
  const hermesResponse = await sendToHermes(text);
  addMessage('assistant', hermesResponse);
  await speak(hermesResponse);
}

// ===== Toggle Microphone =====
function toggleMic() {
  if (isListening) {
    stopListening();
  } else if (isProcessing || isSpeaking) {
    return;
  } else {
    startListening();
  }
}

// ===== Start listening (SpeechRecognition API) =====
function startListening() {
  if (!speechSupported) {
    setStatus('error', 'Voice not supported in this browser. Type instead.');
    setTimeout(() => setStatus('idle', 'Type your message below'), 2000);
    return;
  }

  try {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'en-US';

    recognition.onresult = (event) => {
      const text = event.results[0][0].transcript;
      addMessage('user', text);
      processAndSpeak(text);
    };

    recognition.onerror = (event) => {
      console.error('Speech recognition error:', event.error);
      if (event.error === 'not-allowed') {
        setStatus('error', 'Microphone access denied. Allow mic permissions in your browser settings.');
      } else if (event.error === 'no-speech') {
        setStatus('idle', 'Tap the mic and speak');
      } else {
        setStatus('error', 'Speech error: ' + event.error);
      }
      setTimeout(() => {
        if (!isProcessing && !isSpeaking) setStatus('idle', 'Tap the mic and speak');
      }, 2000);
    };

    recognition.onend = () => {
      if (isListening && !isProcessing && !isSpeaking) {
        setStatus('idle', 'Tap the mic and speak');
      }
    };

    recognition.start();
    setStatus('listening', 'Listening...');
  } catch (e) {
    console.error('Mic error:', e);
    setStatus('error', 'Could not start microphone. Try typing instead.');
    setTimeout(() => setStatus('idle', 'Type your message below'), 2000);
  }
}

function stopListening() {
  if (recognition) {
    try { recognition.stop(); } catch(e) {}
    recognition = null;
  }
  if (!isProcessing && !isSpeaking) {
    setStatus('idle', 'Tap the mic and speak');
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

// ===== Init =====
detectSpeechSupport();
setTimeout(() => setStatus('idle', 'Tap the mic and speak'), 500);
