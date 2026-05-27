"""LeadRescuePro Voice Bridge v6 — Real-time voice via WebSocket + Pipecat agent.
Run: uvicorn voice_bridge_v2:app --host 0.0.0.0 --port 8643
"""
import json, os, uuid, asyncio, time
from pathlib import Path
from fastapi import FastAPI, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
import urllib.request, urllib.error

app = FastAPI(title="LeadRescuePro Voice Bridge v6")
HERMES_API = "http://127.0.0.1:8642/v1/chat/completions"
PIPECAT_AGENT = "ws://127.0.0.1:8765/v2/ws"
HERMES_MODEL = os.getenv("HERMES_VOICE_MODEL", "hermes-agent")
VOICE_NAME = os.getenv("HERMES_VOICE_NAME", "en-US-AndrewMultilingualNeural")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

os.makedirs(STATIC_DIR, exist_ok=True)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

HTML_PATH = os.path.join(os.path.dirname(__file__), "voice_bridge.html")

# --- Lazy-loaded Whisper model ---
_whisper_model = None

def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        print("[Bridge] Loading Whisper tiny model...")
        t0 = time.time()
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        print(f"[Bridge] Whisper loaded in {time.time()-t0:.1f}s")
    return _whisper_model


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


@app.get("/")
async def index():
    try:
        with open(HTML_PATH, "r") as f:
            content = f.read()
    except:
        content = "<html><body><h1>Error loading interface</h1></body></html>"
    return HTMLResponse(content=content, status_code=200)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "lrp-voice-bridge-v6"}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    try:
        payload = {
            "model": HERMES_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the LeadRescuePro voice assistant. You help Fahim manage "
                        "his lead generation business for US plumbing companies. You have full "
                        "access to all his tools, data, and systems. Keep responses conversational "
                        "and concise since this is a voice conversation. Answer naturally like a "
                        "human would speak. Be helpful, direct, and use plain language. "
                        "Keep responses under 3 sentences when possible — this is being read aloud."
                    )
                },
                {"role": "user", "content": req.message}
            ],
            "stream": False,
            "max_tokens": 2000
        }

        data = json.dumps(payload).encode()
        http_req = urllib.request.Request(
            HERMES_API, data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(http_req, timeout=120) as resp:
                result = json.loads(resp.read())
                response_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not response_text:
                    response_text = "I'm sorry, I couldn't process that. Could you try again?"
        except urllib.error.HTTPError as e:
            response_text = f"I'm sorry, I'm having trouble connecting. Error code: {e.code}"
        except urllib.error.URLError:
            response_text = "I'm sorry, the system seems to be busy. Please try again in a moment."

        return {"response": response_text}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/tts")
async def text_to_speech(req: Request):
    try:
        body = await req.json()
        text = body.get("text", "")
        if not text:
            return JSONResponse(status_code=400, content={"error": "No text provided"})

        filename = f"tts_{uuid.uuid4().hex[:12]}.mp3"
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
            return JSONResponse(status_code=500, content={"error": "TTS timed out"})

        if proc.returncode != 0 or not os.path.exists(filepath):
            return JSONResponse(status_code=500, content={"error": "TTS failed"})

        return {"audio_url": f"/static/{filename}", "text": text}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Smart transcription: Whisper fast pass + Hermes/Codex correction."""
    try:
        t_start = time.time()
        audio_bytes = await file.read()
        ext = Path(file.filename).suffix if file.filename else ".webm"
        tmp_path = os.path.join(STATIC_DIR, f"in_{uuid.uuid4().hex[:8]}{ext}")
        with open(tmp_path, "wb") as f:
            f.write(audio_bytes)

        model = get_whisper()
        segments, info = model.transcribe(
            tmp_path, beam_size=5, language=None, vad_filter=True,
        )
        whisper_text = " ".join([s.text for s in segments]).strip()

        correction_prompt = (
            "You are a speech transcription corrector. The text below is a raw "
            "Whisper speech-to-text output that may have errors — especially with "
            "technical terms, names, and accented speech. Fix any errors and return "
            "only the corrected text. Keep the speaker's original words and meaning.\n\n"
            f"Raw transcript: \"{whisper_text}\"\n\n"
            "Corrected transcript:"
        )

        payload = {
            "model": HERMES_MODEL,
            "messages": [
                {"role": "system", "content": "You correct speech-to-text transcription errors. Return only the corrected text, nothing else."},
                {"role": "user", "content": correction_prompt}
            ],
            "stream": False,
            "max_tokens": 500
        }

        corrected_text = whisper_text
        try:
            data = json.dumps(payload).encode()
            http_req = urllib.request.Request(
                HERMES_API, data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(http_req, timeout=30) as resp:
                result = json.loads(resp.read())
                corrected = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if corrected and len(corrected) > 5:
                    corrected_text = corrected
        except Exception:
            pass

        try:
            os.remove(tmp_path)
        except:
            pass

        duration = time.time() - t_start
        return {
            "whisper_raw": whisper_text,
            "transcript": corrected_text,
            "language": info.language if info else "en",
            "duration_s": round(duration, 1),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.websocket("/v2/ws")
async def websocket_proxy_v2(websocket: WebSocket):
    """Proxy WebSocket to RT voice agent (JSON protocol)."""
    await websocket.accept()
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(PIPECAT_AGENT) as agent_ws:
                async def to_agent():
                    try:
                        while True:
                            data = await websocket.receive_text()
                            await agent_ws.send_str(data)
                    except (WebSocketDisconnect, Exception):
                        pass

                async def to_client():
                    try:
                        while True:
                            msg = await agent_ws.receive()
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await websocket.send_text(msg.data)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await websocket.send_bytes(msg.data)
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                break
                    except (WebSocketDisconnect, Exception):
                        pass

                await asyncio.gather(to_agent(), to_client())
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[Bridge] WS proxy v2 error: {e}")
    finally:
        try:
            await websocket.close()
        except:
            pass


@app.websocket("/ws")
async def websocket_proxy(websocket: WebSocket):
    """Proxy WebSocket — legacy handler (accepts both text and binary)."""
    await websocket.accept()
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(PIPECAT_AGENT) as agent_ws:
                async def to_agent():
                    try:
                        while True:
                            data = await websocket.receive()
                            if data["type"] == "websocket.disconnect":
                                break
                            text_data = data.get("text")
                            bytes_data = data.get("bytes")
                            if text_data is not None:
                                await agent_ws.send_str(text_data)
                            elif bytes_data is not None:
                                await agent_ws.send_bytes(bytes_data)
                    except (WebSocketDisconnect, Exception):
                        pass

                async def to_client():
                    try:
                        while True:
                            msg = await agent_ws.receive()
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await websocket.send_text(msg.data)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await websocket.send_bytes(msg.data)
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                break
                    except (WebSocketDisconnect, Exception):
                        pass

                await asyncio.gather(to_agent(), to_client())
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[Bridge] WS proxy error: {e}")
    finally:
        try:
            await websocket.close()
        except:
            pass
