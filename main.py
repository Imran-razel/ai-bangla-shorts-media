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
MAX_TTS_ATTEMPTS = 3


def ms_to_srt_time(ms: float) -> str:
    h = int(ms // 3600000)
    m = int((ms % 3600000) // 60000)
    s = int((ms % 60000) // 1000)
    msec = int(ms % 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"


def get_base_url() -> str:
    external = os.environ.get("RENDER_EXTERNAL_URL")
    if external:
        return external.rstrip("/")
    return "http://localhost:8000"


async def run_tts_once(script: str, voice: str, audio_path: Path):
    """Run a single edge_tts pass. Returns (words, audio_chunks)."""
    words = []
    audio_chunks = 0
    communicate = edge_tts.Communicate(script, voice)
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
                audio_chunks += 1
            elif chunk["type"] == "WordBoundary":
                words.append(chunk)
    return words, audio_chunks


async def generate_tts_with_retry(script: str, voice: str, audio_path: Path):
    """
    edge_tts's websocket connection to Microsoft's endpoint can drop mid-stream
    (common on free-tier hosts like Render). Retry a few times with backoff,
    and treat a stream that produced very little audio relative to script
    length as a failed attempt, not a success.
    """
    script_len = len(script)
    last_error = None
    last_words = []
    last_chunks = 0

    for attempt in range(1, MAX_TTS_ATTEMPTS + 1):
        try:
            words, audio_chunks = await run_tts_once(script, voice, audio_path)
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            words, audio_chunks = [], 0

        audio_size = audio_path.stat().st_size if audio_path.exists() else 0
        last_words, last_chunks = words, audio_chunks

        stream_too_short = script_len > 200 and audio_chunks < 5

        if audio_size > 0 and not stream_too_short:
            return words, audio_chunks, audio_size, None

        last_error = last_error or (
            f"stream ended early (audio_chunks={audio_chunks}, "
            f"audio_size={audio_size}, script_len={script_len})"
        )

        if attempt < MAX_TTS_ATTEMPTS:
            await asyncio.sleep(1.5 * attempt)

    audio_size = audio_path.stat().st_size if audio_path.exists() else 0
    return last_words, last_chunks, audio_size, last_error


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
    words, audio_chunks, audio_size, error = await generate_tts_with_retry(
        req.script, voice, audio_path
    )

    if audio_size == 0:
        raise HTTPException(
            500,
            f"TTS produced zero bytes after {MAX_TTS_ATTEMPTS} attempts. "
            f"voice={voice} script_len={len(req.script)} error={error}"
        )

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
        "audio_chunks": audio_chunks,
        "audio_bytes": audio_size,
        "warning": error,
    }


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

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(req.broll_url)
        if resp.status_code != 200:
            raise HTTPException(400, f"Failed to download broll_url: HTTP {resp.status_code}")
        broll_path.write_bytes(resp.content)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio_path)],
        capture_output=True, text=True
    )
    if probe.returncode != 0:
        raise HTTPException(500, f"ffprobe failed: {probe.stderr}")
    audio_dur = probe.stdout.strip()

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
