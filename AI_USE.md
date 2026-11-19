# AI Coding Tools & Methodology Disclosure (AI_USE.md)

In accordance with IncuBrix Assessment Rule 1 (*"Disclose everything: list every AI coding tool you used and for what"*), this document outlines all AI coding assistants and models utilized in designing, building, and validating this service.

---

## 1. AI Coding Assistants Used

| Tool / Model | Version / Platform | Specific Tasks & Responsibilities |
| :--- | :--- | :--- |
| **Antigravity / Gemini 3.8 Flash** | Google Antigravity Agentic IDE | Architecture review, Docker & MinIO orchestration, OpenAPI 3.1 YAML schema authoring, and FFmpeg smart-crop filter design. |
| **Claude 3.5 Sonnet / ChatGPT (GPT-4o)** | Web & IDE Assistant | Drafting initial scaffolding for SQLite idempotency hashing, test case generation for hard cases, and regex double-meaning disambiguation rules. |
| **GitHub Copilot** | VS Code Extension | Real-time code completions, typing definitions, docstrings, and Pydantic field validators. |

---

## 2. System AI Components Inside the Shipped Engine

The following AI models and engines are embedded inside the service runtime:

1. **Automated Speech Recognition (ASR)**:
   - **Model**: `faster-whisper` (`base` model, CTranslate2 INT8 quantization, MIT License).
   - **Usage**: Transcribes spoken audio from remote URLs and local file uploads, generating word-level timestamps used to anchor scene transitions.
2. **Text-to-Speech (TTS)**:
   - **Engine**: `edge-tts` (Microsoft Neural TTS via WebSocket, free & commercial-safe) with local fallback to OS speech synthesis (`pyttsx3`).
   - **Usage**: Produces human-quality voiceovers with precise word boundaries in English (`en-IN-female`), Hindi (`hi-IN-female`), and Tamil (`ta-IN-female`).
3. **Semantic Scene Understanding & Guardrails**:
   - **Engine**: Rule-based keyword disambiguation + Indic-to-English translation mapping (optional Gemini 2.5 Flash adapter supported via `LLM_PROVIDER=gemini`).
   - **Usage**: Resolves double meanings (e.g. *cloud* -> server data center, *python* -> code screen), maps Hindi/Tamil queries to English concepts, and enforces safety guardrails on sensitive events and named brands.
4. **Automated Relevance Scoring**:
   - **Engine**: Metadata & concept overlap scoring algorithm (scaled 1–5) and multimodal LLM evaluation.
   - **Usage**: Records relevance metrics for every scene inside `timeline.json` and reports aggregate score in `manifest.json`.

---

## 3. Candidate Authorship & Understanding

All core business logic, FFmpeg compositing chains, error handling structures, idempotency enforcement, and API contracts were inspected, debugged, and verified by the candidate. The candidate is prepared to explain and modify any component during the 60–90 minute live technical session.