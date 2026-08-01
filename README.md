# AI Bangla Shorts - Media Service (Render.com, Free)

FastAPI service: TTS (edge-tts) + video assembly (ffmpeg), replaces n8n Execute Command nodes.

## Deploy Steps (Free, No Card)

1. GitHub-এ নতুন repo বানান, এই ৪টা ফাইল push করুন:
   `main.py`, `requirements.txt`, `Dockerfile`, `render.yaml`

2. https://render.com → Sign up (GitHub দিয়ে) → **New +** → **Blueprint**
   → repo সিলেক্ট করুন → Render নিজেই `render.yaml` পড়ে ফ্রি সার্ভিস বানাবে

3. Deploy শেষ হলে একটা public URL পাবেন, যেমন:
   `https://ai-bangla-shorts-media.onrender.com`

4. টেস্ট করুন:
   ```
   curl https://ai-bangla-shorts-media.onrender.com/health
   ```

## ⚠️ Free Tier Limitation (গুরুত্বপূর্ণ)

Render free tier **15 মিনিট inactivity-তে sleep** করে। প্রথম রিকোয়েস্টে ৩০-৫০ সেকেন্ড "cold start" লাগতে পারে।
n8n HTTP Request নোডে timeout কমপক্ষে **120 সেকেন্ড** সেট করুন।

## Endpoints

### POST /tts
```json
{ "script": "বাংলা স্ক্রিপ্ট টেক্সট..." }
```
Response:
```json
{ "job_id": "abc123", "audio_url": "https://.../files/abc123/audio.mp3", "srt_url": "https://.../files/abc123/captions.srt", "word_count": 42 }
```

### POST /assemble
```json
{ "job_id": "abc123", "broll_url": "https://pexels-video-link.mp4" }
```
Response:
```json
{ "job_id": "abc123", "final_video_url": "https://.../files/abc123/final.mp4" }
```

## Note on Storage
Render free tier এর ফাইল সিস্টেম **ephemeral** — service restart/redeploy হলে পুরনো ফাইল মুছে যায়।
প্রতিটা pipeline run এ generate করা ভিডিও n8n থেকে সাথে সাথে ডাউনলোড/আপলোড (YouTube/FB/TikTok/Telegram) করে ফেলতে হবে।
