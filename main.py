audio_path = job_dir / "audio.mp3"
srt_path = job_dir / "captions.srt"

voice = req.voice or VOICE
communicate = edge_tts.Communicate(req.script, voice)
words = []
try:
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append(chunk)
except Exception as e:
    raise HTTPException(500, f"edge_tts stream failed: {type(e).__name__}: {e}")

audio_size = audio_path.stat().st_size if audio_path.exists() else 0
if audio_size == 0:
    raise HTTPException(500, f"TTS failed completely: no audio bytes written (voice={voice})")

if not words:
    # audio আছে কিন্তু word boundary নাই — SRT ছাড়া চালিয়ে যাও, error না দিয়ে
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("")
else:
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, w in enumerate(words, start=1):
            start = w["offset"] / 10000
            end = (w["offset"] + w["duration"]) / 10000
            f.write(f"{i}\n{ms_to_srt_time(start)} --> {ms_to_srt_time(end)}\n{w['text']}\n\n")
