# IncuBrix Script & Voiceover to B-Roll Engine

A containerised microservice that turns any voiceover audio file, text script, or structured scene list into finished, commercially-licensed B-roll videos across multiple aspect ratios (`16:9`, `9:16`, `1:1`) with synced `.srt` subtitles, burned-in captions, selective scene-swapping, and a complete provenance manifest.

---

## 1. Architecture

```text
Client (JSON / Audio File)
        │  [POST /api/v1/broll/jobs + Idempotency-Key]
        ▼
FastAPI Gateway & SQLite Store
  ├── Validation, Idempotency Check, Rate Limiter (Token Bucket)
  └── Structured JSON Logging (X-Correlation-Id + job_id)
        │
        ▼ (In-process Job Dispatch: FastAPI BackgroundTasks (not a durable queue))
Pipeline Stages:
  1. Audio Ingestion:
     ├── Text Script ──► Edge-TTS / SAPI5 (Word-Level Timestamps)
     └── Audio URL/File ──► Faster-Whisper ASR + 16kHz Mono RMS Energy Check
  2. Semantic Scene Planner:
     ├── Sentence Boundary Segmentation (aligned to voice timestamps)
     ├── Keyword Disambiguation ("cloud computing" -> servers, "python" -> code)
     ├── Indic Concept Mapping (Hindi 'hi-IN' & Tamil 'ta-IN' -> English queries)
     └── Sensitive Topic & Brand Guardrails (calm visuals, no fake logos)
  3. Keyless Commercial Stock Footage Engine:
     ├── Source A (Primary): Wikimedia Commons Video API (CC0 / CC-BY / Public Domain)
     ├── Source B (Failover): Internet Archive Open Video API (Public Domain / CC0)
     └── Source C (Fallback): IncuBrix Kinetic Motion Card Generator (Original Asset)
  4. Video Normalization & Compositing (FFmpeg):
     ├── Framerate (30 fps) & Codec (H.264/AAC, yuv420p, constant framerate)
     ├── Smart Aspect Ratio Cropping (16:9 landscape, 9:16 vertical, 1:1 square)
     └── Subtitles (.srt export + Optional Font-Styled Burn-In)
  5. Manifest & Storage:
     └── Outputs Uploaded to S3-compatible Storage (MinIO) or Local Directory
```

---

## 2. Quickstart: Setup in Under 10 Commands

### Option A: Run via Docker Compose (Recommended)

```bash
# 1. Clone repository
git clone https://github.com/marvameraj/incubrix-broll-engine.git
cd incubrix-broll-engine

# 2. Copy environment file
cp .env.example .env

# 3. Start API and MinIO storage services
docker compose up -d --build

# 4. Check readiness
curl http://localhost:8000/api/v1/ready
```

### Option B: Run Locally

```bash
# 1. Create and activate virtual environment
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate # Linux / macOS

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the development server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 3. How to Run Tests

The automated test suite uses mock adapters and does not replace a real-provider integration run:

```bash
# Run full test suite
pytest -v
```

---

## 4. How to Run the Batch Runner

The command-line batch runner tests folders of audio inputs or JSON test suites against the running API:

```bash
# Run against the 10-case evaluation test suite
python -m cli.batch_runner --url http://127.0.0.1:8000 --inputs test_inputs.json --out batch_results

# Run against a directory of audio files
python -m cli.batch_runner --url http://127.0.0.1:8000 --inputs ./test_audio_folder --out batch_results
```

Summary results and logs are exported directly to `batch_results/batch_summary.json`.

---

## 5. Configuration & Environment Variables

All settings are driven via environment variables or `.env`:

| Setting | Default | Description |
| :--- | :--- | :--- |
| `APP_ENV` | `development` | Environment mode (`development` or `production`) |
| `API_AUTH_TOKEN` | `incubrix-demo-token` | Bearer token required when `APP_ENV=production` |
| `RATE_LIMIT_PER_MINUTE` | `300` | Maximum requests per minute per IP |
| `LLM_PROVIDER` | `mock` | Semantic planner (`mock` or `gemini`) |
| `FOOTAGE_PROVIDER` | `wikimedia` | Primary stock video provider (`wikimedia` or `mock`) |
| `TTS_PROVIDER` | `edge` | Text-to-speech engine (`edge`, `sapi`, or `mock`) |
| `ASR_PROVIDER` | `whisper` | Speech-to-text transcriber (`whisper` or `mock`) |
| `WHISPER_MODEL_SIZE` | `base` | Faster-Whisper model size (`tiny`, `base`, `small`) |
| `MAX_AUDIO_DURATION_SEC` | `600` | Maximum audio duration limit (10 minutes) |
| `MIN_SCENE_DURATION_SEC` | `2.0` | Minimum scene clip duration to avoid visual flashing |
| `STORAGE_BACKEND` | `local` | Output file storage (`s3` for MinIO or `local`) |
| `S3_ENDPOINT_URL` | `http://minio:9000` | S3-compatible MinIO endpoint |
| `S3_BUCKET` | `incubrix-broll` | S3 bucket name for output assets |

---

## 6. Standardized Error Codes (`UPPER_SNAKE_CASE`)

All API errors return JSON adhering to the Integration Standard:
```json
{
  "error": {
    "code": "EMPTY_SCRIPT",
    "message": "Script cannot be empty.",
    "retryable": false,
    "details": {}
  }
}
```

| Error Code | HTTP Status | Retryable | Description |
| :--- | :---: | :---: | :--- |
| `MISSING_IDEMPOTENCY_KEY` | 400 | No | Header `Idempotency-Key` missing from POST request |
| `IDEMPOTENCY_KEY_REUSED` | 409 | No | Key already submitted with a different request payload |
| `EMPTY_INPUT` | 400 | No | Request did not include audio_url, script, or scenes |
| `EMPTY_SCRIPT` | 400 | No | Text script is blank or whitespace |
| `EMPTY_AUDIO` | 400 | No | Uploaded audio file has zero bytes |
| `SILENT_AUDIO` | 400 | No | Audio track RMS speech energy is below threshold |
| `NO_SPEECH_DETECTED` | 400 | No | ASR could not detect intelligible speech words |
| `AUDIO_TOO_LONG` | 400 | No | Audio duration exceeds `MAX_AUDIO_DURATION_SEC` (10m) |
| `INVALID_FILE_TYPE` | 400 | No | File format is not supported (allowed: mp3, wav, m4a) |
| `INVALID_ASPECT_RATIO` | 400 | No | Requested ratio is not in `["16:9", "9:16", "1:1"]` |
| `INVALID_URL` | 400 | No | URL is not HTTP/HTTPS or has invalid hostname |
| `SSRF_BLOCKED` | 400 | No | Private or loopback IP address blocked for security |
| `JOB_NOT_FOUND` | 404 | No | Requested `job_id` does not exist |
| `JOB_NOT_READY` | 400 | No | Cannot swap scene before job has succeeded |
| `INVALID_SCENE_INDEX` | 400 | No | Target scene index is outside timeline range |
| `RATE_LIMITED` | 429 | Yes | Client exceeded requests per minute |
| `SERVICE_UNAVAILABLE` | 503 | Yes | System dependencies (FFmpeg or storage) not ready |
| `INTERNAL_PIPELINE_ERROR`| 500 | Yes | Unhandled error during video rendering |