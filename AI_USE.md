# AI Tooling Disclosure

This project was built using AI-assisted software engineering workflows alongside runtime generative AI models.

## 1. Runtime AI Components in the Application

- **Google Gemini (`gemini-2.5-flash`)**: Performs audio transcription, sentence-boundary scene segmentation, contextual disambiguation of double-meaning terms, and safe visual query generation (`app/adapters/providers.py`).
- **Microsoft Edge Neural TTS (`edge-tts`)**: Converts script text into natural-sounding voiceover narration (`en-IN`, `hi-IN`, `ta-IN`).
- **OpenCV (`opencv-python-headless`)**: Runs Haar-cascade face detection and edge-saliency analysis for subject-centered `9:16` and `1:1` video cropping (`app/compositor.py`).

## 2. Development AI Assistance

- **AI Coding Assistant (Gemini)**: Used to scaffold FastAPI boilerplate, OpenAPI specification, FFmpeg filter chains, and `pytest` integration tests. All architecture decisions, adapter interfaces, error handling, and test runs were reviewed, executed, and verified locally.