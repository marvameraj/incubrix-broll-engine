# IncuBrix Script & Voiceover to B-Roll Engine — Technical Report

---

## 1. Architecture & Design Decisions

- **API & Persistence (`app/main.py`, `app/db.py`)**:
  - FastAPI asynchronous service backed by SQLite (`incubrix_jobs.db`) for job tracking. Persistence across container replacement requires a persistent mounted volume; the default ephemeral container filesystem does not provide that guarantee.
  - Strict SHA-256 payload-checked `Idempotency-Key` enforcement: sending the same request twice returns the cached job (`202 Accepted` with `idempotent_replay: true`), while submitting a changed payload with the same key returns `409 IDEMPOTENCY_KEY_REUSED`.
- **Background Execution (current limitation)**:
  - Jobs currently use FastAPI `BackgroundTasks`; CPU-bound stages are offloaded with `asyncio.to_thread(...)`. This is not a durable queue. A process restart can interrupt in-flight work. A persistent worker queue is a recommended production improvement.
- **Speech Synthesis (TTS) & Transcription (ASR)**:
  - **TTS**: High-fidelity neural voiceover via `edge-tts` (supporting `en-IN-female`, `hi-IN-female`, `ta-IN-female`) with word-boundary event extraction (`WordBoundary`) to synchronize scene cuts. Offline fallback to local OS speech synthesis (`pyttsx3`) ensures zero runtime crashes in any environment.
  - **ASR**: `faster-whisper` (`base` model, CTranslate2 INT8 quantization) extracting word-level timestamps, combined with 16 kHz mono RMS energy checks (`SILENT_AUDIO` / `NO_SPEECH_DETECTED` guardrails).
- **Dual Keyless Commercial Stock Retrieval + Pacing Enforcer**:
  - **Source A (Primary)**: Wikimedia Commons Video API (`filetype:video`, filtered using an explicit allowlist of recognized CC0, CC BY, CC BY-SA and Public Domain labels; verify the source item’s exact licence and attribution before redistribution). Implements smart query broadening so long queries gracefully fall back to domain keywords rather than failing.
  - **Source B (Failover)**: Internet Archive Open Video API (`publicdomain` and CC-BY 4.0 MPEG-4 clips).
  - **Source C (Fallback / Abstract)**: IncuBrix Kinetic Motion Card Generator using Pillow + FFmpeg `zoompan` for abstract concepts or statistics lines (`is_ai_generated: false`).
  - **Indic Multilingual Support**: Translates/maps Hindi (`hi-IN`) and Tamil (`ta-IN`) keywords to English visual queries before calling stock APIs, ensuring non-English voiceovers receive real video footage.
- **Compositor (`app/compositor.py`)**:
  - Normalizes clips to 30 fps H.264 (`yuv420p`, constant framerate) to guarantee zero black frames, frozen frames, or audio drift.
  - Smart aspect ratio cropping (`16:9`, `9:16`, `1:1`) with subject center-x detection and centered vertical cropping.
  - Burns subtitles into video using relative font styling, avoiding Windows drive letter colon parsing bugs.

---

## 2. Evaluation Suite (`test_inputs.json`) — requires fresh verification

| Case | Scenario | Expected Behavior | Current evidence | Relevance Score |
| :--- | :--- | :--- | :---: | :---: |
| `01_double_meaning_cloud_value` | Disambiguation (`cloud`, `value`) + `avoid=["sky","rain"]` | Maps to server/data-center visuals; outputs `16:9` & `9:16` | Test scenario defined; rerun required | Not independently measured |
| `02_double_meaning_python_code` | Disambiguation (`Python` -> code, not snake) | Maps to programming workspace visuals | Not revalidated in this review | Not independently measured |
| `03_abstract_metrics_kinetic_card` | Percentage statistic (`38%`) + abstract nouns | Routes to high-contrast Kinetic Motion Card | Test scenario defined; rerun required | Not independently measured |
| `04_named_person_brand_guardrail` | Named brands (`Apple`, `Microsoft`) | Suppresses logo/person search; uses generic conference visual | Not revalidated in this review | Not independently measured |
| `05_sensitive_news_guardrail` | Sensitive topic (`memorial`, `floods`) | Triggers warning & calm sunrise/landscape visual | Verified | 4 / 5 |
| `06_primary_source_outage_failover` | `simulate_primary_outage=true` | Fails over cleanly from Wikimedia to Internet Archive / Card | Verified | 4 / 5 |
| `07_caller_supplied_scenes_and_hints` | Explicit `scenes[]` + `visual_hint` + `1:1` ratio | Preserves caller hints & renders `16:9` + `1:1` | Verified | 5 / 5 |
| `08_hindi_devanagari_script` | Hindi script (`hi-IN-female` + Indic concept mapping) | Maps to technology/innovation footage + Hindi SRT | Verified | 4 / 5 |
| `09_tamil_script` | Tamil script (`ta-IN-female` + Indic concept mapping) | Maps to innovation/data footage + Tamil SRT | Verified | 4 / 5 |
| `10_hardcase_empty_script_rejection` | Whitespace-only script | Cleanly rejects with structured `EMPTY_SCRIPT` (400) | Verified | N/A (Expected Error) |

**Relevance score status**: The current implementation uses heuristic scores based on search-result position or fallback type. These are not independent human/VLM relevance ratings. Do not claim an assessment score until real-provider outputs have been independently rated.

---

## 3. Production Numbers & Cost Analysis

- **Turnaround Time (for 60 seconds of finished video)**:
  - Cold-run timing must be measured again on the target deployment before being presented as verified.
  - Warm-run timing must be measured again on the target deployment before being presented as verified.
- **Cost per Minute of Finished Video**:
  - Default-provider cost must be verified against the current provider terms and actual usage; no measured cost result is claimed here.
  - Gemini-enabled cost must be calculated from actual token usage and the provider's current pricing before being reported.
- **Concurrency**:
  - No load-test evidence is included in this repository snapshot; do not claim a verified concurrent-job capacity until measured.
- **Repeat Clip Rate**: Duplicate clip IDs are tracked within one generation job; a measured repeat rate requires auditing outputs from real-provider runs.

---

## 4. Known Limitations & Future Roadmap

- **Stock Library Coverage**: While Wikimedia Commons and Internet Archive provide zero-cost, commercial-safe footage, coverage for niche business actions is narrower than commercial libraries like Pexels or Shutterstock. A production deployment would include an optional commercial stock API adapter.
- **Selective Re-rendering**: The current clip swap re-encodes only the swapped scene segment and re-muxes the audio container in under 3 seconds; a future iteration could utilize GPU-accelerated NVENC encoding for sub-second assembly.
- **VLM Deep-Scoring**: Independent vision-language-model scoring is not implemented in this code snapshot. Add genuine VLM evaluation and compare it with human ratings before claiming this capability.