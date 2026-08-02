import base64
import os
import subprocess
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="AI Bangla Shorts - Media Service")

BASE_DIR = Path("/tmp/ai_shorts")
BASE_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/files", StaticFiles(directory=str(BASE_DIR)), name="files")

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel, multilingual-capable
ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"


def ms_to_srt_time(sec: float) -> str:
    total_ms = int(sec * 1000)
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    msec = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"


def get_base_url() -> str:
    external = os.environ.get("RENDER_EXTERNAL_URL")
    if external:
        return external.rstrip("/")
    return "http://localhost:8000"


def build_word_srt(characters, char_starts, char_ends) -> str:
    """Group character-level alignment into word-level SRT entries."""
    entries = []
    word_chars = []
    word_start = None
    word_end = None
    for ch, start, end in zip(characters, char_starts, char_ends):
        if ch.strip() == "":
            if word_chars:
                entries.append((word_start, word_end, "".join(word_chars)))
                word_chars = []
                word_start = None
                word_end = None
            continue
        if word_start is None:
            word_start = start
        word_end = end
        word_chars.append(ch)
    if word_chars:
        entries.append((word_start, word_end, "".join(word_chars)))

    lines = []
    for i, (start, end, word) in enumerate(entries, start=1):
        lines.append(f"{i}\n{ms_to_srt_time(start)} --> {ms_to_srt_time(end)}\n{word}\n")
    return "\n".join(lines)


class TTSRequest(BaseModel):
    script: str
    voice_id: str | None = None


@app.post("/tts")
async def generate_tts(req: TTSRequest):
    if not req.script or not req.script.strip():
        raise HTTPException(400, "script is required")
    if not ELEVENLABS_API_KEY:
        raise HTTPException(500, "ELEVENLABS_API_KEY not set in environment")

    job_id = uuid.uuid4().hex[:12]
    job_dir = BASE_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    audio_path = job_dir / "audio.mp3"
    srt_path = job_dir / "captions.srt"

    voice_id = req.voice_id or ELEVENLABS_VOICE_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "accept": "application/json",
    }
    payload = {
        "text": req.script,
        "model_id": ELEVENLABS_MODEL_ID,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(
            resp.status_code,
            f"ElevenLabs TTS failed: {resp.status_code} {resp.text[:500]}"
        )

    data = resp.json()
    audio_b64 = data.get("audio_base64")
    alignment = data.get("alignment") or {}

    if not audio_b64:
        raise HTTPException(500, f"ElevenLabs response missing audio_base64: {str(data)[:500]}")

    audio_bytes = base64.b64decode(audio_b64)
    if len(audio_bytes) == 0:
        raise HTTPException(500, "ElevenLabs returned zero audio bytes")

    audio_path.write_bytes(audio_bytes)

    characters = alignment.get("characters", [])
    char_starts = alignment.get("character_start_times_seconds", [])
    char_ends = alignment.get("character_end_times_seconds", [])

    word_count = 0
    if characters and char_starts and char_ends:
        srt_content = build_word_srt(characters, char_starts, char_ends)
        srt_path.write_text(srt_content, encoding="utf-8")
        word_count = srt_content.count("\n\n") + (1 if srt_content.strip() else 0)
    else:
        srt_path.write_text("", encoding="utf-8")

    base = get_base_url()
    return {
        "job_id": job_id,
        "audio_url": f"{base}/files/{job_id}/audio.mp3",
        "srt_url": f"{base}/files/{job_id}/captions.srt",
        "word_count": word_count,
        "audio_bytes": len(audio_bytes),
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
