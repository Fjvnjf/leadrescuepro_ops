"""LeadRescuePro Real-Time Voice Agent — Pipecat.
Whisper STT + DeepSeek LLM + Kokoro TTS. All local, zero API keys.

Usage:
  python3 voice_agent_pipecat.py           # Start server on :8765
  # Connect via: ws://localhost:8765/ws

The agent accepts streaming audio via WebSocket and returns
streaming audio responses — real-time voice conversation.
"""
import asyncio
import json
import os
import sys

from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_response import (
    LLMFullResponseAggregator,
)
from pipecat.services.deepseek.llm import DeepSeekLLMService
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

app = FastAPI(title="LeadRescuePro Real-Time Voice Agent")

SYSTEM_PROMPT = (
    "You are the LeadRescuePro voice assistant. You help Fahim manage "
    "his lead generation business for US plumbing companies. Keep responses "
    "conversational, concise, and natural — like a human speaking. "
    "Keep responses under 3 sentences. Be helpful and direct."
)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time voice WebSocket — full duplex streaming."""
    await websocket.accept()
    logger.info("WebSocket connected")

    try:
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_sample_rate=16000,
                audio_out_sample_rate=24000,
                add_wav_header=False,
                vad_enabled=True,
                vad_audio_passthrough=True,
            ),
        )

        stt = WhisperSTTService(
            model="base",
            device="cpu",
            compute_type="int8",
            language="en",
        )

        llm = DeepSeekLLMService(
            api_key="not-needed",
            model="deepseek-v4-pro",
            base_url="http://127.0.0.1:8642/v1",
        )

        tts = KokoroTTSService(
            model="kokoro-v0_19",
            voice="af_heart",
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        tma_in = LLMFullResponseAggregator()
        tma_out = LLMFullResponseAggregator()

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                tma_in,
                llm,
                tma_out,
                tts,
                transport.output(),
            ]
        )

        task = PipelineTask(pipeline)
        await task.run()

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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "lrp-real-time-voice"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    logger.info(f"Starting LeadRescuePro Real-Time Voice Agent on port {port}")
    logger.info(f"WebSocket endpoint: ws://localhost:{port}/ws")
    uvicorn.run(app, host="0.0.0.0", port=port)
