# Media & Model Sources and Licensing

## 1. Stock Video Footage Providers (Allowlisted Commercial-Safe Only)
1. **Wikimedia Commons Video API (`[https://commons.wikimedia.org/w/api.php](https://commons.wikimedia.org/w/api.php)`)**
   - **Selection & Verification**: Queries `filetype:video` and enforces a strict allowlist of verified commercial-use licenses (`CC0`, `CC0 1.0`, `Public Domain`, `PD`, `CC-BY-3.0`, `CC-BY-4.0`, `CC-BY-SA-3.0`, `CC-BY-SA-4.0`). Clips with missing, unknown, `-NC` (Non-Commercial), or `-ND` (No-Derivatives) licenses are rejected.
2. **Internet Archive Open Video API (`[https://archive.org/advancedsearch.php](https://archive.org/advancedsearch.php)`)**
   - **Selection & Verification**: Secondary commercial-safe video source filtered strictly to `publicdomain/zero`, `publicdomain/mark`, `licenses/by/3.0`, `licenses/by/4.0`, `licenses/by-sa/3.0`, and `licenses/by-sa/4.0`.
3. **IncuBrix Kinetic Motion Generator (Local Pillow + FFmpeg)**
   - **License**: 100% original generated motion graphics (`is_ai_generated: false`), used for abstract/statistic lines and offline fallback.

## 2. Speech Synthesis (TTS) & Transcription (ASR)
- **TTS**: Local OS speech synthesis via `pyttsx3` (Windows SAPI5 / Linux `espeak-ng`), commercially usable offline with zero third-party redistribution restrictions.
- **ASR**: `faster-whisper` (`base` model, MIT License) running locally on CPU for word-level audio transcription.