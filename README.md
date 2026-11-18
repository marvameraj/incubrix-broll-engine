# IncuBrix Script & Voiceover to B-Roll Video Engine

An asynchronous microservice that transforms raw narration scripts, structured scene lists, or voiceover audio files (`.mp3`, `.wav`, `.m4a`) into rendered B-roll videos (`16:9`, `9:16`, `1:1`) with synced captions (`.srt` and burned-in), clip provenance (`timeline.json`), and execution telemetry (`manifest.json`).

## Architecture Overview

1. **API & Job Layer (`app/main.py`)**: FastAPI service exposing `POST /api/v1/broll/jobs`, `POST /api/v1/broll/jobs/upload`, `GET /api/v1/jobs/{job_id}`, `POST /api/v1/broll/jobs/{job_id}/scenes/{scene_index}/swap`, `/health`, and `/ready`. Supports `Idempotency-Key` deduplication, sliding-window rate limiting, correlation IDs (`X-Correlation-Id`), Bearer auth in production, and HMAC-SHA256 webhook callbacks.
2. **Swappable Provider Adapters (`app/adapters/providers.py`)**:
   - **TTS**: `edge-tts` (Indian English, Hindi, Tamil neural voices) or deterministic offline `mock` synthesizer.
   - **Semantic Scene Planner**: Gemini 2.5 Flash multimodal audio/text planner (or deterministic offline rule engine in `mock` mode) that splits scenes on sentence boundaries, resolves double-meaning words by context, and enforces visual safety guardrails.
   - **Dual-Source Footage Router**: Routes concrete scenes to Pixabay Stock Video API (skipping already-used clip IDs to guarantee zero repetition) and routes abstract/statistic scenes or primary API failures to an original local Kinetic Motion Card generator (Pillow + FFmpeg Ken Burns zoom).
3. **Smart Video Compositor (`app/compositor.py`)**: Uses OpenCV Haar-cascade face detection and Canny edge-energy saliency to compute horizontal subject centers before cropping `16:9` clips to `9:16` or `1:1`. Normalizes resolution, `30 fps`, color contrast (`eq`), generates `captions.srt`, and caches per-scene segments for fast single-scene replacement.
4. **Storage Abstraction (`app/storage.py`)**: Supports local filesystem (`/files/...`) and MinIO S3-compatible object storage with presigned URLs.

## Quick Start (Docker Compose)

```bash
cp .env.example .env
docker compose up --build -d