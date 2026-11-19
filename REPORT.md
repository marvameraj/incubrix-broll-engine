# IncuBrix Script & Voiceover to B-Roll Engine — Technical Report

## 1. Architecture & Design Decisions
- **API & Persistence (`app/main.py`, `app/db.py`)**: FastAPI asynchronous service backed by SQLite (`incubrix_jobs.db`) for durable job status and SHA-256 payload-checked `Idempotency-Key` enforcement (`409 IDEMPOTENCY_KEY_REUSED` on payload mismatch).
- **Non-Blocking Execution**: Blocking CPU/FFmpeg/ASR operations run inside `asyncio.to_thread(...)` so concurrent users and status polling remain responsive under load.
- **Speech & Transcription (`app/adapters/providers.py`)**:
  - **Text-to-Speech**: Local commercially-safe OS speech synthesis (`pyttsx3` / SAPI5) with optional `edge-tts` adapter and automatic failover.
  - **Speech-to-Text (ASR)**: `faster-whisper` (`compute_type="int8"`) producing word-level timestamps for `audio_url` and `/upload` inputs, paired with 16 kHz mono RMS energy checks (`SILENT_AUDIO` / `NO_SPEECH_DETECTED` guardrails).
- **Dual Keyless Stock Retrieval + Kinetic Fallback**:
  - **Source A**: Wikimedia Commons Video API (`filetype:video`, filtered to exclude `-NC` and `-ND` licenses).
  - **Source B**: Internet Archive Open Video API (`publicdomain` / `CC0` / `CC-BY 4.0` MPEG-4 clips).
  - **Source C**: Unicode-capable (Nirmala/Noto) wrapped Kinetic Motion Generator via Pillow + FFmpeg for abstract/statistic lines and offline resilience.
- **Compositor (`app/compositor.py`)**: Normalizes clips to 30 fps H.264, applies smart center/subject cropping for `16:9`, `9:16`, and `1:1`, generates `.srt` captions, and burns subtitles when `captions.burn_in=true`.

## 2. 10-Case Evaluation Suite (`test_inputs.json`)
| Case | Scenario | Expected Behavior | Status | Relevance Score |
| :--- | :--- | :--- | :--- | :--- |
| `01_double_meaning_cloud_value` | Context disambiguation (`cloud`, `value`) + `avoid=["sky","rain"]` | Maps to server/data-center visuals; outputs `16:9` & `9:16` | Verified | `not_scored` (manual: 4/5) |
| `02_double_meaning_python_code` | Context disambiguation (`Python` -> code, not snake) | Maps to programming workspace visuals | Verified | `not_scored` (manual: 4/5) |
| `03_abstract_metrics_kinetic_card` | Percentage statistic (`38%`) + abstract nouns | Routes to wrapped high-contrast Kinetic Motion Card | Verified | `not_scored` (manual: 5/5) |
| `04_named_person_brand_guardrail` | Named brands (`Apple`, `Microsoft`) | Suppresses logo/person search; uses generic conference visual | Verified | `not_scored` (manual: 4/5) |
| `05_sensitive_news_guardrail` | Sensitive topic (`memorial`, `floods`) | Triggers warning & calm symbolic visual | Verified | `not_scored` (manual: 4/5) |
| `06_primary_source_outage_failover` | `simulate_primary_outage=true` | Fails over cleanly from Wikimedia Commons to Internet Archive / Card | Verified | `not_scored` (manual: 4/5) |
| `07_caller_supplied_scenes_and_hints` | Explicit `scenes[]` + `visual_hint` + `1:1` ratio | Preserves caller hints & renders `16:9` + `1:1` | Verified | `not_scored` (manual: 5/5) |
| `08_hindi_devanagari_script` | Hindi script (`।` danda splitting + Unicode font rendering) | Splits on `।` and renders readable Devanagari cards/SRT | Verified | `not_scored` (manual: 4/5) |
| `09_tamil_script` | Tamil script (`ta-IN`) | Renders readable Tamil cards/SRT without tofu boxes | Verified | `not_scored` (manual: 4/5) |
| `10_hardcase_empty_script_rejection` | Whitespace-only script | Cleanly fails with structured `EMPTY_SCRIPT` error | Verified | N/A (Expected Error) |

*Note on automated scoring*: Automated VLM frame-by-frame clip scoring is not enabled in the default CPU runtime; therefore `timeline.json` explicitly reports `"relevance_score": "not_scored"` rather than emitting synthetic scores.

## 3. Known Limitations & Honest Self-Assessment
- **Automated VLM Relevance Scoring**: Not integrated (`"not_scored"` in `timeline.json`); visual relevance relies on context disambiguation rules and human spot-checks.
- **Scene Swap Speed**: Re-encodes the updated segment and re-muxes the final container with updated `.srt` captions; faster than re-fetching all scenes, though full container re-mux scales linearly with total video duration.
- **Colour Normalization**: Uses deterministic FFmpeg normalization rather than scene-by-scene histogram matching.
- **TTS Prosody**: Local SAPI5 (`pyttsx3`) is commercially safe and offline-resilient, though less expressive than paid cloud neural voices.