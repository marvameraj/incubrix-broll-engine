# Media Sources, Licensing & Commercial Safety Policy

All visual and audio assets produced or retrieved by this engine are cleared for commercial use and logged per scene in `timeline.json` and `manifest.json`.

## 1. Visual Sources

| Source | Provider / Endpoint | License Terms | Attribution Requirement | Commercial Use |
| :--- | :--- | :--- | :--- | :--- |
| **Source A: Pixabay Video API** | `https://pixabay.com/api/videos/` | Pixabay Content License | Not required (logged in `timeline.json` for transparency) | Allowed |
| **Source B: Kinetic Motion Card Generator** | Local Pillow + FFmpeg (`app/adapters/providers.py`) | Original Programmatic Asset | Generated locally (`is_ai_generated: true` flagged in `timeline.json`) | Allowed |

## 2. Audio & AI Models

- **Edge-TTS (`edge-tts`)**: Neural text-to-speech synthesis used for narration generation when text/scene input is provided without pre-recorded audio.
- **Google Gemini (`gemini-2.5-flash`)**: Used for multimodal audio transcription, scene boundary segmentation, and semantic visual query generation.

## 3. Safety & Provenance Guarantees

- `safesearch=true` is enforced on all external stock video queries.
- No watermarked or editorial-only stock footage is ingested.
- Every output bundle includes `timeline.json` containing `clip_id`, `source`, `license`, `attribution`, `url`, and `is_ai_generated` for every scene.