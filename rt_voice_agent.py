"""LeadRescuePro Real-Time Voice Agent — WebSockets with JSON protocol.
Simple, low-latency: browser sends audio chunks, gets text then TTS audio back.

Protocol (JSON over WebSocket):
  Server -> {"type":"transcript","text":"..."}
  Server -> {"type":"reply","text":"..."}
  Server -> {"type":"tts","url":"..."}
  Server -> {"type":"state","status":"thinking|done"}
  Client -> {"type":"transcribe","audio":"<base64>","format":"webm|wav|pcm"}
  Client -> {"type":"ping"}
"""

import asyncio
import json
import os
import base64
import time
import uuid
import io
import wave
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
import uvicorn

log_file = os.path.join(os.path.dirname(__file__), "rt_agent.log")
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True,
)
logger = logging.getLogger("rt_agent")

import sys
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
logger.addHandler(handler)

logger.info("Starting RT Voice Agent")

app = FastAPI(title="LeadRescuePro RT Voice")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static_rt")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/audio", StaticFiles(directory=STATIC_DIR), name="audio")

_whisper_model = None

def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        logger.info("Loading Whisper tiny model...")
        t0 = time.time()
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        logger.info(f"Whisper loaded in {time.time()-t0:.1f}s")
    return _whisper_model

HERMES_API = "http://127.0.0.1:8642/v1/chat/completions"
HERMES_MODEL = os.getenv("HERMES_VOICE_MODEL", "hermes-agent")
VOICE_NAME = os.getenv("HERMES_VOICE_NAME", "en-US-AndrewMultilingualNeural")
SYSTEM_PROMPT = (
    "You are the LeadRescuePro voice assistant helping Fahim manage "
    "his lead generation business for US plumbing companies. Keep responses "
    "conversational, concise, and natural. Keep responses under 3 sentences."
)


async def transcribe_pcm(audio_bytes: bytes) -> str:
    """Transcribe PCM/WAV audio bytes using Whisper."""
    import numpy as np
    model = get_whisper()
    try:
        with io.BytesIO(audio_bytes) as buf:
            with wave.open(buf, 'rb') as wf:
                frames = wf.readframes(wf.getnframes())
                audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = model.transcribe(audio, beam_size=3, language="en", vad_filter=True)
    return " ".join([s.text for s in segments]).strip()


async def get_ai_response(text: str) -> str:
    """Get AI response from Hermes API."""
    import urllib.request
    payload = {
        "model": HERMES_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ],
        "stream": False,
        "max_tokens": 2000
    }
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            HERMES_API, data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        logger.error(f"AI response error: {e}")
        return "I'm sorry, I couldn't process that."


async def get_tts(text: str) -> str:
    """Generate TTS audio file, return URL."""
    filename = f"rt_{uuid.uuid4().hex[:12]}.mp3"
    filepath = os.path.join(STATIC_DIR, filename)
    proc = await asyncio.create_subprocess_exec(
        "edge-tts", "--voice", VOICE_NAME,
        "--text", text, "--write-media", filepath,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        return ""
    if proc.returncode != 0 or not os.path.exists(filepath):
        logger.warning(f"TTS failed: exit={proc.returncode}")
        return ""
    return f"/audio/{filename}"


@app.websocket("/v2/ws")
async def voice_websocket(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connected")
    session_id = uuid.uuid4().hex[:8]

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            msg_type = data.get("type", "")

            if msg_type == "transcribe":
                audio_b64 = data.get("audio", "")
                audio_fmt = data.get("format", "webm")
                t0 = time.time()
                audio_bytes = base64.b64decode(audio_b64)
                logger.info(f"Received {len(audio_bytes)} bytes audio ({audio_fmt})")

                if audio_fmt == "webm":
                    tmp = f"/tmp/rt_audio_{session_id}.webm"
                    with open(tmp, "wb") as f:
                        f.write(audio_bytes)
                    model = get_whisper()
                    segments, _ = model.transcribe(tmp, beam_size=3, language="en", vad_filter=True)
                    transcript = " ".join([s.text for s in segments]).strip()
                    os.remove(tmp)
                else:
                    transcript = await transcribe_pcm(audio_bytes)

                stt_time = time.time() - t0
                logger.info(f"STT ({stt_time:.1f}s): {transcript[:80]}")

                if not transcript:
                    logger.info("No speech detected, skipping")
                    continue

                await websocket.send_text(json.dumps({"type": "transcript", "text": transcript}))
                await websocket.send_text(json.dumps({"type": "state", "status": "thinking"}))

                reply = await get_ai_response(transcript)
                logger.info(f"AI reply ({stt_time:.1f}s): {reply[:60]}")

                await websocket.send_text(json.dumps({"type": "reply", "text": reply}))

                tts_url = await get_tts(reply)
                if tts_url:
                    await asyncio.sleep(0.2)
                    await websocket.send_text(json.dumps({"type": "tts", "url": tts_url}))
                    logger.info(f"TTS: {tts_url}, total: {time.time()-t0:.1f}s")
                else:
                    await websocket.send_text(json.dumps({"type": "state", "status": "done"}))

            elif msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            await websocket.close()
        except:
            pass


RT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>LeadRescuePro RT Voice</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:#0a0e17;color:#c8d6e5;
  min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px
}
.container{max-width:480px;width:100%;text-align:center}
.header{margin-bottom:24px}
.header h1{font-size:22px;font-weight:700;color:#00d4ff;text-shadow:0 0 20px rgba(0,212,255,0.15)}
.header p{font-size:13px;color:#5a7a9a;margin-top:4px}
.st{display:flex;align-items:center;justify-content:center;gap:8px;font-size:14px;margin-bottom:8px;min-height:24px}
.sd{display:inline-block;width:10px;height:10px;border-radius:50%}
.sd.i{background:#5a7a9a}
.sd.ls{background:#00d4ff;animation:pl 1.5s infinite}
.sd.th{background:#ffa502}
.sd.sp{background:#2ed573}
@keyframes pl{0%,100%{opacity:1}50%{opacity:.4}}
.mb{
  width:140px;height:140px;border-radius:50%;
  border:3px solid #1a2a44;
  background:linear-gradient(135deg,#11192e 0%,#0f1629 100%);
  cursor:pointer;display:flex;align-items:center;justify-content:center;
  margin:16px auto;transition:all .3s;position:relative;
  -webkit-tap-highlight-color:transparent
}
.mb svg{width:56px;height:56px;fill:#5a7a9a}
.mb.ac{border-color:#00d4ff;box-shadow:0 0 50px rgba(0,212,255,.25);animation:br 1.5s ease-in-out infinite}
.mb.ac svg{fill:#00d4ff}
.mb.th{border-color:#ffa502}
.mb.th svg{fill:#ffa502}
.mb.sp{border-color:#2ed573}
.mb.sp svg{fill:#2ed573}
@keyframes br{0%,100%{transform:scale(1)}50%{transform:scale(1.06)}}
.ti{font-size:13px;color:#5a7a9a;margin-bottom:16px;min-height:40px}
.lv{display:flex;flex-direction:column;gap:8px;margin-bottom:16px;min-height:80px;max-height:300px;overflow-y:auto}
.ms{background:linear-gradient(135deg,#11192e 0%,#0f1629 100%);border:1px solid #1a2a44;border-radius:12px;padding:12px 16px;text-align:left;font-size:14px;line-height:1.5}
.ms .u{color:#00d4ff;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px}
.ms .h{color:#2ed573}
.stats{font-size:11px;color:#3a4a5a;margin-top:8px;display:flex;gap:16px;justify-content:center}
.stats span{display:flex;align-items:center;gap:4px}
audio{display:none}
.cta{font-size:12px;color:#5a7a9a;margin-top:16px}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>LeadRescuePro</h1>
    <p>Real-Time Voice</p>
  </div>
  <div class="st">
    <span class="sd i" id="dot"></span>
    <span id="lbl">Hold to talk</span>
  </div>
  <button class="mb" id="mic">
    <svg viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
  </button>
  <div class="ti" id="ti">Hold the mic button and speak. Release when done.</div>
  <div class="lv" id="lv"></div>
  <div class="stats">
    <span id="stStat">STT: --</span>
    <span id="aiStat">AI: --</span>
    <span id="ttsStat">TTS: --</span>
  </div>
  <div class="cta">Release to send &mdash; AI responds with voice</div>
  <audio id="ap"></audio>
</div>
<script>
(function(){
"use strict";
var ws=null, dot=document.getElementById('dot'), lbl=document.getElementById('lbl');
var mb=document.getElementById('mic'), lv=document.getElementById('lv');
var ap=document.getElementById('ap'), ti=document.getElementById('ti');
var stStat=document.getElementById('stStat'), aiStat=document.getElementById('aiStat'), ttsStat=document.getElementById('ttsStat');
var recorder=null, stream=null, chunks=[];
var listening=false, processing=false;

function setSt(s,t){
  dot.className='sd '+s; lbl.textContent=t;
  mb.className='mb';
  if(s==='ls'){mb.classList.add('ac');listening=true;}
  else if(s==='th'){mb.classList.add('th');}
  else if(s==='sp'){mb.classList.add('sp');}
  else {listening=false;}
}

function addMsg(label,text){
  var d=document.createElement('div'); d.className='ms';
  d.innerHTML='<div class="'+label+'">'+(label==='u'?'You':'LeadRescuePro')+'</div><div>'+esc(text)+'</div>';
  lv.appendChild(d); lv.scrollTop=lv.scrollHeight;
}

function esc(s){
  var d=document.createElement('div'); d.textContent=s; return d.innerHTML;
}

function connect(){
  var proto=window.location.protocol==='https:'?'wss:':'ws:';
  var host=window.location.host;
  ws=new WebSocket(proto+'//'+host+'/v2/ws');
  ws.onopen=function(){
    ti.textContent='Connected. Hold mic to talk.';
    setSt('i','Hold to talk');
  };
  ws.onclose=function(){
    ti.textContent='Disconnected. Reconnecting...';
    setSt('i','Reconnecting');
    setTimeout(connect,2000);
  };
  ws.onerror=function(e){
    ti.textContent='Connection error. Retrying... ('+e.type+')';
    setTimeout(connect,3000);
  };
  ws.onmessage=function(e){
    try{
      var msg=JSON.parse(e.data);
      if(msg.type==='transcript'){
        addMsg('u',msg.text);
      }else if(msg.type==='reply'){
        addMsg('h',msg.text);
        aiStat.textContent='AI: done';
      }else if(msg.type==='tts'){
        var audioPath=msg.url;
        ap.src=window.location.origin+audioPath;
        ap.onended=function(){ setSt('i','Hold to talk'); processing=false; };
        ap.play().catch(function(){ setSt('i','Hold to talk'); processing=false; });
        setSt('sp','Speaking...');
        ttsStat.textContent='TTS: done';
      }else if(msg.type==='state'){
        if(msg.status==='thinking'){setSt('th','Thinking...');}
        else if(msg.status==='done'){setSt('i','Hold to talk'); processing=false;}
      }
    }catch(e){}
  };
}

function startRecord(){
  if(processing||listening)return;
  chunks=[];
  navigator.mediaDevices.getUserMedia({audio:{
    sampleRate:16000,channelCount:1,echoCancellation:true,noiseSuppression:true
  }}).then(function(s){
    stream=s;
    recorder=new MediaRecorder(s,{mimeType:'audio/webm;codecs=opus'});
    recorder.ondataavailable=function(e){if(e.data.size>0)chunks.push(e.data);};
    recorder.onstop=function(){
      stream.getTracks().forEach(function(t){t.stop();});
      if(chunks.length>0){
        var blob=new Blob(chunks,{type:'audio/webm'});
        sendAudio(blob);
      }else{setSt('i','Hold to talk');}
    };
    recorder.start();
    setSt('ls','Listening...');
    ti.textContent='Recording... release when done';
  }).catch(function(){
    ti.textContent='Mic access denied.';
    setSt('i','Mic blocked');
  });
}

function sendAudio(blob){
  processing=true;
  stStat.textContent='STT: sending...';
  setSt('th','Sending audio...');
  var reader=new FileReader();
  reader.onload=function(){
    ws.send(JSON.stringify({type:'transcribe',audio:reader.result.split(',')[1],format:'webm'}));
  };
  reader.readAsDataURL(blob);
}

function stopRecord(){
  if(recorder&&recorder.state==='recording'){
    recorder.stop();
    recorder=null;
  }
}

mb.addEventListener('mousedown',function(e){e.preventDefault();startRecord();});
mb.addEventListener('mouseup',function(e){e.preventDefault();stopRecord();});
mb.addEventListener('mouseleave',function(e){if(recorder&&recorder.state==='recording')stopRecord();});
mb.addEventListener('touchstart',function(e){e.preventDefault();startRecord();});
mb.addEventListener('touchend',function(e){e.preventDefault();stopRecord();});
mb.addEventListener('touchcancel',function(e){if(recorder&&recorder.state==='recording')stopRecord();});

document.addEventListener('keydown',function(e){
  if(e.target.tagName!=='INPUT'&&e.code==='Space'){e.preventDefault();startRecord();}
});
document.addEventListener('keyup',function(e){
  if(e.code==='Space'){stopRecord();}
});

connect();
addMsg('h','Hi! Hold mic to talk, release when done.');
})();
</script>
</body>
</html>"""


@app.get("/rt")
async def rt_page():
    return HTMLResponse(content=RT_HTML, status_code=200)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "lrp-rt-voice-v2"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    logger.info(f"Starting on port {port}")
    logger.info(f"RT page: http://localhost:{port}/rt")
    uvicorn.run(app, host="0.0.0.0", port=port)
