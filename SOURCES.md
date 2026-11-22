# Media & Model Sources and Licensing

## 1. Stock Video Footage Providers (Allowlisted Commercial-Safe Only)
1. **Wikimedia Commons Video API (`[https://commons.wikimedia.org/w/api.php](https://commons.wikimedia.org/w/api.php)`)**
   - **Selection & Verification**: Queries `filetype:video` and accepts only recognized `CC0`, `Public Domain`, `CC BY 3.0/4.0`, and `CC BY-SA 3.0/4.0` labels. Missing, unknown, non-commercial, and no-derivatives labels are rejected. The original file page and attribution must still be checked before redistribution.
2. **Internet Archive Open Video API (`[https://archive.org/advancedsearch.php](https://archive.org/advancedsearch.php)`)**
   - **Selection & Verification**: Secondary source candidates are accepted only when the returned licence URL ends in one of the explicitly supported public-domain, CC BY, or CC BY-SA paths. Confirm the item's rights statement and attribution on its archive page before redistribution.
3. **IncuBrix Kinetic Motion Generator (Local Pillow + FFmpeg)**
   - **License**: 100% original generated motion graphics (`is_ai_generated: false`), used for abstract/statistic lines and offline fallback.

## 2. Speech Synthesis (TTS) & Transcription (ASR)
- **TTS**: `edge-tts` is the configured neural TTS path; if it fails, the application attempts local `pyttsx3` speech synthesis (Windows SAPI5 / Linux `espeak-ng`). Review the selected provider's current terms before commercial use.
- **ASR**: `faster-whisper` (`base` model, MIT License) running locally on CPU for word-level audio transcription.