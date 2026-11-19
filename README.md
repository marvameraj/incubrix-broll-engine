# IncuBrix Script & Voiceover to B-Roll Engine

Automated service that converts a plain-text script, structured scene list, or spoken audio file into multi-aspect-ratio B-roll videos (`16:9`, `9:16`, `1:1`) with synced `.srt` subtitles, burn-in captions, commercial-license manifests, and single-scene swap support.

## Architecture
```text
Client (JSON / Audio Upload)
        │
        ▼
FastAPI + SQLite Store (Idempotency, Rate Limit, Correlation-Id)
        │
        ├──► TTS (SAPI5 / Edge) or Whisper ASR (Word Timestamps + RMS Energy Check)
        ├──► Semantic Scene Planner (Disambiguation, Guardrails, Min-Duration Merge)
        ├──► Dual Stock Retrieval (Wikimedia Commons ──► Internet Archive ──► Kinetic Card)
        └──► FFmpeg Compositor (Smart Crop 16:9 / 9:16 / 1:1 + SRT Burn-In)