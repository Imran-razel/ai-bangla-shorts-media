import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

import edge_tts
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="AI Bangla Shorts - Media Service")

BASE_DIR = Path("/tmp/ai_shorts")
BASE_DIR.mkdir(parents=True, exist_ok=True)

# Serve generated files publicly at /files/<job_id>/...
app.mount("/files", StaticFiles(directory=str(BASE_DIR)), name="files")

VOICE = "bn-BD-NabanitaNeural"


def ms_to_srt_time(ms: float) -> str:
    h = int(ms // 3600000)
    m = int((ms % 3600000) // 60000)
    s = int((ms % 60000) // 1000)
    msec = int(ms % 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"


def get_base_url() -> str:
    # Render.com sets this automatically. Fallback for local testing.
    external = os.environ.get("RENDER_EXTERNAL_URL")
    if external:
        return external.rstrip("/")
    return "http://localhost:8000"


# ---------------------- TTS ----------------------

class TTSRequest(BaseModel):
    script: str
    voice: str | None = None


@app.post("/tts")
async def generate_tts(req: TTSRequest):
    if not req.script or not req.script.strip():
        raise HTTPException(400, "script is required")

    job_id = uuid.uuid4().hex[:12]
    job_dir = BASE_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    audio_path = job_dir / "audio.mp3"
    srt_path = job_dir / "captions.srt"

    voice = req.voice or VOICE
    communicate = edge_tts.Communicate(req.script, voice)
    words = []
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append(chunk)

    if not words:
        raise HTTPException(500, "TTS produced no word boundaries; audio may be empty")

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, w in enumerate(words, start=1):
            start = w["offset"] / 10000
            end = (w["offset"] + w["duration"]) / 10000
            f.write(f"{i}\n{ms_to_srt_time(start)} --> {ms_to_srt_time(end)}\n{w['text']}\n\n")

    base = get_base_url()
    return {
        "job_id": job_id,
        "audio_url": f"{base}/files/{job_id}/audio.mp3",
        "srt_url": f"{base}/files/{job_id}/captions.srt",
        "word_count": len(words),
    }


# ---------------------- ASSEMBLE ----------------------

class AssembleRequest(BaseModel):
    job_id: str
    broll_url: str


@app.post("/assemble")
async def assemble_video(req: AssembleRequest):
    job_dir = BASE_DIR / req.job_id
    audio_path = job_dir / "audio.mp3"
    srt_path = job_dir / "captions.srt"

    if not audio_path.exists() or not srt_path.exists():
        raise HTTPException(404, f"job_id {req.job_id} not found or /tts not run first")

    broll_path = job_dir / "broll.mp4"
    final_path = job_dir / "final.mp4"

    # Download broll video
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(req.broll_url)
        if resp.status_code != 200:
            raise HTTPException(400, f"Failed to download broll_url: HTTP {resp.status_code}")
        broll_path.write_bytes(resp.content)

    # Get audio duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio_path)],
        capture_output=True, text=True
    )
    if probe.returncode != 0:
        raise HTTPException(500, f"ffprobe failed: {probe.stderr}")
    audio_dur = probe.stdout.strip()

    # Assemble with ffmpeg: loop broll, burn subtitles, mux audio
    vf = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"subtitles={srt_path}:force_style='FontName=Noto Sans Bengali,FontSize=16,"
        f"PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,BorderStyle=3,Alignment=2,MarginV=120'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(broll_path),
        "-i", str(audio_path),
        "-t", audio_dur,
        "-vf", vf,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-c:a", "aac",
        "-shortest", str(final_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(500, f"ffmpeg failed: {result.stderr[-2000:]}")

    base = get_base_url()
    return {
        "job_id": req.job_id,
        "final_video_url": f"{base}/files/{req.job_id}/final.mp4",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"service": "AI Bangla Shorts Media Service", "endpoints": ["/tts", "/assemble", "/health"]}
