# AI & Automation Disclosure

1. **Automated Speech Recognition (ASR)**: Uses `faster-whisper` (MIT-licensed CTranslate2 implementation of OpenAI Whisper) locally on CPU to transcribe uploaded/linked audio and extract word-level timestamps.
2. **Text-to-Speech (TTS)**: Default runtime uses local OS speech synthesis (`pyttsx3` / SAPI5 / `espeak-ng`) for deterministic, commercially-safe offline voiceover generation.
3. **Scene Planning & Footage Selection**: Uses deterministic rule-based semantic disambiguation and strict commercial-license allowlisting against Wikimedia Commons and Internet Archive APIs (optional Gemini 2.5 Flash adapter is available when `LLM_PROVIDER=gemini` and `GEMINI_API_KEY` are configured).
4. **Relevance Scoring**: Automated VLM scoring is not enabled in the default CPU configuration; `timeline.json` explicitly reports `"relevance_score": "not_scored"` rather than fabricating scores.