import asyncio
import json
import math
import os
import re
import struct
import subprocess
import wave
import httpx
import edge_tts
from PIL import Image, ImageDraw, ImageFont
from google import genai
from app.config import settings
from app.errors import AppError
from app.logging_utils import logger

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
ARCHIVE_SEARCH_API = "https://archive.org/advancedsearch.php"
USER_AGENT = "IncuBrixBrollEngine/1.0 (https://github.com/marvameraj/incubrix-broll-engine)"


# 1. TTS ADAPTER (Text -> Audio + True Word-Boundary Timestamps)
# 1. TTS ADAPTER (Text -> Audio + True Word-Boundary Timestamps)
def _synthesize_pyttsx3(text: str, voice: str, out_path: str) -> None:
    import pyttsx3
    engine = pyttsx3.init()
    voices = engine.getProperty("voices")
    want_female = "female" in voice.lower()
    for v in voices:
        name_low = (v.name or "").lower()
        if want_female and any(k in name_low for k in ("zira", "heera", "female")):
            engine.setProperty("voice", v.id)
            break
        if not want_female and any(k in name_low for k in ("david", "ravi", "male")):
            engine.setProperty("voice", v.id)
            break
    engine.setProperty("rate", 165)
    raw_wav = out_path + ".sapi.wav"
    engine.save_to_file(text, raw_wav)
    engine.runAndWait()
    subprocess.run(
        ["ffmpeg", "-y", "-i", raw_wav, "-ar", "44100", "-ac", "2", out_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if os.path.exists(raw_wav):
        os.remove(raw_wav)


async def synthesize_speech(
    text: str,
    voice: str,
    out_path: str,
    word_timings_out: list[dict] | None = None,
) -> float:
    if not text or not text.strip():
        raise AppError("EMPTY_SCRIPT", "Input script cannot be empty.", status_code=400)

    clean_text = text.strip()
    words = clean_text.split()

    if settings.tts_provider == "mock":
        duration = max(3.0, min(float(settings.max_audio_duration_sec), len(words) * 0.42))
        await asyncio.to_thread(
            subprocess.run,
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
                "-af", "volume=0.05", "-ar", "44100", "-ac", "2", out_path,
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if word_timings_out is not None:
            step = duration / max(1, len(words))
            word_timings_out.extend(
                {"word": w, "start": round(i * step, 3), "end": round((i + 1) * step, 3)}
                for i, w in enumerate(words)
            )
        return duration

    if settings.tts_provider == "edge":
        try:
            voice_map = {
                "en-IN-female": "en-IN-NeerjaNeural",
                "en-IN-male": "en-IN-PrabhatNeural",
                "en-US-female": "en-US-JennyNeural",
                "hi-IN-female": "hi-IN-SwaraNeural",
                "hi-IN-male": "hi-IN-MadhurNeural",
                "ta-IN-female": "ta-IN-PallaviNeural",
                "ta-IN-male": "ta-IN-ValluvarNeural",
            }
            selected_voice = voice_map.get(voice, voice if "-" in voice else "en-IN-NeerjaNeural")
            mp3_tmp = out_path + ".mp3"
            communicate = edge_tts.Communicate(clean_text, selected_voice)
            with open(mp3_tmp, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary" and word_timings_out is not None:
                        start_s = chunk["offset"] / 10_000_000.0
                        dur_s = chunk["duration"] / 10_000_000.0
                        word_timings_out.append({"word": chunk["text"], "start": round(start_s, 3), "end": round(start_s + dur_s, 3)})

            await asyncio.to_thread(
                subprocess.run,
                ["ffmpeg", "-y", "-i", mp3_tmp, "-ar", "44100", "-ac", "2", out_path],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if os.path.exists(mp3_tmp):
                os.remove(mp3_tmp)
            return get_media_duration(out_path)
        except Exception as exc:
            logger.warning(f"edge-tts failed ({exc}); failing over to local SAPI5 speech engine")

    # Commercially-safe local OS speech synthesis (pyttsx3 / SAPI5)
    await asyncio.to_thread(_synthesize_pyttsx3, clean_text, voice, out_path)
    dur = get_media_duration(out_path)
    if word_timings_out is not None and not word_timings_out:
        total_chars = sum(max(1, len(w)) for w in words)
        cur = 0.0
        for w in words:
            w_dur = dur * (max(1, len(w)) / total_chars)
            word_timings_out.append({"word": w, "start": round(cur, 3), "end": round(cur + w_dur, 3)})
            cur += w_dur
    return dur
def get_media_duration(path: str) -> float:
    res = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        capture_output=True, text=True,
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise AppError("INVALID_MEDIA_FORMAT", "Could not read media duration or corrupted file.", status_code=400)
    dur = float(res.stdout.strip())
    if dur <= 0.2:
        raise AppError("INVALID_AUDIO", "Audio file has zero duration.", status_code=400)
    if dur > settings.max_audio_duration_sec:
        raise AppError("AUDIO_TOO_LONG", f"Audio exceeds max duration ({settings.max_audio_duration_sec}s).", status_code=400)

    # Check RMS speech energy via 16kHz mono PCM conversion
    probe_wav = path + ".energy.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-ar", "16000", "-ac", "1", "-t", "30", "-c:a", "pcm_s16le", probe_wav],
            capture_output=True, check=True,
        )
        with wave.open(probe_wav, "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            if len(raw) >= 2:
                count = len(raw) // 2
                samples = struct.unpack(f"<{count}h", raw[: count * 2])
                rms = math.sqrt(sum(s * s for s in samples) / max(1, count))
                if rms < 30.0:
                    raise AppError("SILENT_AUDIO", "Audio track is silent (no speech energy detected).", status_code=400)
    finally:
        if os.path.exists(probe_wav):
            os.remove(probe_wav)
    return dur


# 2. ASR TRANSCRIBER (Whisper with word timestamps for audio_url / upload)
def transcribe_audio_with_timestamps(audio_path: str) -> tuple[str, list[dict]]:
    if getattr(settings, "asr_provider", "whisper") == "mock":
        dur = get_media_duration(audio_path)
        text = "Cloud computing teams build reliable software systems with measurable business impact."
        words = text.split()
        step = dur / len(words)
        timings = [{"word": w, "start": round(i * step, 3), "end": round((i + 1) * step, 3)} for i, w in enumerate(words)]
        return text, timings

    from faster_whisper import WhisperModel
    model_size = getattr(settings, "whisper_model_size", "base")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, word_timestamps=True)
    texts, timings = [], []
    for seg in segments:
        texts.append(seg.text.strip())
        if seg.words:
            for w in seg.words:
                timings.append({"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)})
    transcript = " ".join(texts).strip()
    if not transcript:
        raise AppError("NO_SPEECH_DETECTED", "Audio contains music or noise without transcribable speech.", status_code=400)
    return transcript, timings


# 3. SEMANTIC SCENE PLANNER (Word-timestamp aligned, min-duration enforced, avoid-filtered)
def _enforce_scene_constraints(scenes: list[dict], duration: float) -> list[dict]:
    min_dur = getattr(settings, "min_scene_duration_sec", 2.0)
    merged: list[dict] = []
    for s in scenes:
        s["start_s"] = round(max(0.0, float(s["start_s"])), 2)
        s["end_s"] = round(min(duration, max(s["start_s"] + 0.5, float(s["end_s"]))), 2)
        if merged and (s["end_s"] - s["start_s"]) < min_dur:
            merged[-1]["end_s"] = s["end_s"]
            merged[-1]["text"] += " " + s["text"]
        else:
            merged.append(s)

    if len(merged) > 1 and (merged[-1]["end_s"] - merged[-1]["start_s"]) < min_dur:
        merged[-2]["end_s"] = merged[-1]["end_s"]
        merged[-2]["text"] += " " + merged[-1]["text"]
        merged.pop()

    for idx, s in enumerate(merged):
        s["index"] = idx
        s["start_s"] = 0.0 if idx == 0 else merged[idx - 1]["end_s"]
        if idx == len(merged) - 1:
            s["end_s"] = round(duration, 2)
    return merged


def plan_scenes(
    script: str | None,
    scenes_input: list[dict] | None,
    audio_path: str,
    duration: float,
    style: str,
    avoid: list[str],
    word_timings: list[dict] | None = None,
) -> list[dict]:
    if duration <= 0.2:
        raise AppError("INVALID_AUDIO", "Audio file has zero duration.", status_code=400)

    avoid_lower = {a.lower() for a in (avoid or [])}

    # If no text script or scenes were provided (audio_url / upload), transcribe first
    if not script and not scenes_input and os.path.exists(audio_path):
        script, word_timings = transcribe_audio_with_timestamps(audio_path)

    # Case A: Pre-supplied IncuBrix scene list
    if scenes_input:
        total_words = sum(max(1, len(s["text"].split())) for s in scenes_input)
        cur = 0.0
        planned = []
        for idx, item in enumerate(scenes_input):
            share = max(1, len(item["text"].split())) / total_words
            seg_dur = duration - cur if idx == len(scenes_input) - 1 else round(duration * share, 2)
            raw_q = item.get("visual_hint") or item["text"]
            clean_q = " ".join(w for w in raw_q.split() if w.lower() not in avoid_lower) or "modern workspace"
            planned.append({
                "index": idx,
                "text": item["text"],
                "start_s": round(cur, 2),
                "end_s": round(cur + seg_dur, 2),
                "search_query": clean_q,
                "scene_type": "concrete",
                "is_sensitive": False,
                "reason": "Matched user-provided visual_hint with avoid filter applied",
            })
            cur += seg_dur
        return _enforce_scene_constraints(planned, duration)

    # Case B: Deterministic Semantic Planner (aligned to word timestamps)
    if settings.llm_provider == "mock" or not settings.gemini_api_key:
        text = (script or "Ambient background scene.").strip()
        sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", text) if s.strip()]
        if not sentences:
            sentences = ["Ambient background scene."]

        planned = []
        w_cursor = 0
        total_w = len(word_timings) if word_timings else 0
        total_words_fallback = sum(max(1, len(s.split())) for s in sentences)
        cur = 0.0

        for idx, sent in enumerate(sentences):
            wc = max(1, len(sent.split()))
            if word_timings and w_cursor < total_w:
                start_s = word_timings[w_cursor]["start"]
                end_idx = min(total_w - 1, w_cursor + wc - 1)
                end_s = word_timings[end_idx]["end"] if idx < len(sentences) - 1 else duration
                w_cursor = end_idx + 1
            else:
                share = wc / total_words_fallback
                seg_dur = duration - cur if idx == len(sentences) - 1 else round(duration * share, 2)
                start_s, end_s = cur, cur + seg_dur
                cur += seg_dur

            query = _resolve_double_meanings_locally(sent, avoid_lower)
            is_abstract = bool(re.search(r"\d+%|\btrust\b|\bstrategy\b|\bgrowth\b|\bvision\b", sent.lower()))
            is_sensitive = any(w in sent.lower() for w in ["tragedy", "died", "crash", "sad", "memorial", "war", "disaster"])
            if is_sensitive:
                query = "sunrise horizon nature calm landscape"
            planned.append({
                "index": idx,
                "text": sent,
                "start_s": round(start_s, 2),
                "end_s": round(end_s, 2),
                "search_query": query,
                "scene_type": "abstract_or_stat" if (is_abstract or is_sensitive) else "concrete",
                "is_sensitive": is_sensitive,
                "reason": f"Aligned to narration timestamps; query='{query}' (avoid={list(avoid_lower)})",
            })
        return _enforce_scene_constraints(planned, duration)

    # Case C: Live Gemini 2.5 Flash Multimodal Audio/Script Planner
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = f"""
You are a video B-roll planner. Total audio duration is {duration:.2f} seconds.
Style: {style}. Avoid terms/topics: {avoid}.
Script: {script or "Transcribe directly from the attached audio."}

Rules:
1. Break narration on sentence boundaries covering 0.0 to {duration:.2f}s with NO gaps or overlaps.
2. Minimum scene length: {getattr(settings, 'min_scene_duration_sec', 2.0)}s.
3. Resolve double meanings by context ("cloud computing" -> "server room data center", "python" -> "software code screen").
4. Never search for real named people or brand logos; use safe generic visuals.
5. Set scene_type to "abstract_or_stat" for numbers/percentages/abstract concepts, else "concrete".

Return ONLY a valid JSON array:
[{{"text": "...", "start_s": 0.0, "end_s": 3.5, "search_query": "concrete 3-5 word visual search", "scene_type": "concrete", "is_sensitive": false, "reason": "why this fits"}}]
"""
    contents = [prompt]
    if os.path.exists(audio_path):
        uploaded = client.files.upload(file=audio_path)
        contents.append(uploaded)

    resp = client.models.generate_content(model=settings.gemini_model, contents=contents)
    raw = resp.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    scenes = json.loads(raw)
    for s in scenes:
        s["search_query"] = " ".join(w for w in s.get("search_query", "").split() if w.lower() not in avoid_lower) or "modern workspace"
    return _enforce_scene_constraints(scenes, duration)


def _resolve_double_meanings_locally(sentence: str, avoid_lower: set[str]) -> str:
    low = sentence.lower()
    if "cloud" in low and any(w in low for w in ["data", "computing", "server", "app", "software"]):
        base = "server room data center technology"
    elif "value" in low or "saving money" in low:
        base = "financial planning laptop chart"
    elif "python" in low and any(w in low for w in ["code", "script", "program", "ai"]):
        base = "programmer typing code screen"
    elif "bank" in low and any(w in low for w in ["river", "water", "stream"]):
        base = "peaceful river shoreline nature"
    elif any(bp in low for bp in ["elon", "modi", "biden", "apple", "google", "microsoft", "tesla"]):
        base = "modern technology conference office"
    else:
        words = [w for w in re.findall(r"[\w\u0900-\u097F\u0B80-\u0BFF]+", low) if len(w) > 3 and w not in avoid_lower][:4]
        base = " ".join(words) or "modern workspace"
    filtered = " ".join(w for w in base.split() if w.lower() not in avoid_lower)
    return filtered or "modern workspace"


# 4. FOOTAGE PROVIDERS (Keyless Wikimedia Commons + Internet Archive + Kinetic Fallback)
async def fetch_clip_for_scene(
    scene: dict,
    used_clip_ids: set[str],
    work_dir: str,
    disable_primary: bool = False,
) -> dict:
    out_clip = os.path.join(work_dir, f"raw_scene_{scene['index']}.mp4")
    dur = max(1.5, scene["end_s"] - scene["start_s"])

    if (
        settings.footage_provider not in ("mock", "")
        and scene.get("scene_type") != "abstract_or_stat"
    ):
        # Source A: Wikimedia Commons Video API (Zero-key CC0 / CC-BY / Public Domain footage)
        if not disable_primary:
            try:
                clip = await _fetch_wikimedia_video(scene["search_query"], dur, out_clip, used_clip_ids)
                if clip:
                    return clip
            except Exception as exc:
                logger.warning(f"Source A (Wikimedia Commons) failed, failing over to Source B: {exc}")

        # Source B: Internet Archive Open Video API (Zero-key CC0 / Public Domain MPEG4 footage)
        try:
            clip = await _fetch_archive_org_video(scene["search_query"], dur, out_clip, used_clip_ids)
            if clip:
                return clip
        except Exception as exc:
            logger.warning(f"Source B (Internet Archive) failed, failing over to Kinetic Motion Card: {exc}")

    # Source C: Deterministic Kinetic Motion Card Generator (Abstract/Stat Scenes or Offline Fallback)
    cid = f"generated_card:{scene['index']}"
    used_clip_ids.add(cid)
    await asyncio.to_thread(_generate_motion_card, scene["text"], scene["index"], dur, out_clip)
    return {
        "local_path": out_clip,
        "clip_id": cid,
        "source": "IncuBrix Kinetic Motion Generator",
        "license": "Original Generated Asset (Commercial Use Allowed)",
        "attribution": "Generated locally via Pillow + FFmpeg",
        "url": "local://kinetic-generator",
        "is_ai_generated": False,
        "selection_rule": "Abstract/stat scene or stock sources unavailable -> Kinetic Motion Card",
        "relevance_score": "not_scored",
    }


async def _fetch_wikimedia_video(query: str, duration: float, out_clip: str, used_clip_ids: set[str]) -> dict | None:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:video {query}",
        "gsrnamespace": 6,
        "gsrlimit": 8,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(WIKIMEDIA_API, params=params)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {}).values()

        for page in pages:
            cid = f"wikimedia:{page.get('pageid')}"
            if cid in used_clip_ids:
                continue
            info = (page.get("imageinfo") or [{}])[0]
            video_url = info.get("url", "")
            if not video_url.lower().endswith((".webm", ".mp4", ".ogv")):
                continue
            meta = info.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "CC-BY / Public Domain")
            if "nc" in lic.lower() or "nd" in lic.lower():
                continue

            raw_dl = out_clip + ".wm_tmp"
            vresp = await client.get(video_url, follow_redirects=True)
            vresp.raise_for_status()
            with open(raw_dl, "wb") as f:
                f.write(vresp.content)

            res = await asyncio.to_thread(
                subprocess.run,
                ["ffmpeg", "-y", "-i", raw_dl, "-t", f"{duration + 1.5:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", out_clip],
                capture_output=True,
            )
            if os.path.exists(raw_dl):
                os.remove(raw_dl)
            if res.returncode != 0:
                continue

            used_clip_ids.add(cid)
            return {
                "local_path": out_clip,
                "clip_id": cid,
                "source": "Wikimedia Commons",
                "license": f"{lic} (Commercial Use Allowed)",
                "attribution": f"{page.get('title', 'Wikimedia Video')} via Wikimedia Commons",
                "url": info.get("descriptionurl", "[https://commons.wikimedia.org/](https://commons.wikimedia.org/)"),
                "is_ai_generated": False,
                "selection_rule": "Rule 1: Primary keyless commercial-safe stock match via Wikimedia Commons",
                "relevance_score": "not_scored",
            }
    return None


async def _fetch_archive_org_video(query: str, duration: float, out_clip: str, used_clip_ids: set[str]) -> dict | None:
    params = {
        "q": f"({query}) AND mediatype:(movies) AND (licenseurl:*publicdomain* OR licenseurl:*zero* OR licenseurl:*by/4.0*)",
        "fl[]": ["identifier", "title", "licenseurl"],
        "rows": 6,
        "page": 1,
        "output": "json",
    }
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(ARCHIVE_SEARCH_API, params=params)
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])

        for doc in docs:
            ident = doc.get("identifier")
            cid = f"archive:{ident}"
            if not ident or cid in used_clip_ids:
                continue
            mresp = await client.get(f"[https://archive.org/metadata/](https://archive.org/metadata/){ident}/files")
            mresp.raise_for_status()
            files = mresp.json().get("result", [])
            mp4s = [f for f in files if str(f.get("name", "")).lower().endswith(".mp4") and int(f.get("size", 0)) < 25_000_000]
            if not mp4s:
                continue

            dl_url = f"[https://archive.org/download/](https://archive.org/download/){ident}/{mp4s[0]['name']}"
            raw_dl = out_clip + ".ia_tmp"
            vresp = await client.get(dl_url, follow_redirects=True)
            vresp.raise_for_status()
            with open(raw_dl, "wb") as f:
                f.write(vresp.content)

            res = await asyncio.to_thread(
                subprocess.run,
                ["ffmpeg", "-y", "-i", raw_dl, "-t", f"{duration + 1.5:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", out_clip],
                capture_output=True,
            )
            if os.path.exists(raw_dl):
                os.remove(raw_dl)
            if res.returncode != 0:
                continue

            used_clip_ids.add(cid)
            return {
                "local_path": out_clip,
                "clip_id": cid,
                "source": "Internet Archive",
                "license": doc.get("licenseurl", "Public Domain / CC-BY (Commercial Use Allowed)"),
                "attribution": f"{doc.get('title', ident)} via Internet Archive",
                "url": f"[https://archive.org/details/](https://archive.org/details/){ident}",
                "is_ai_generated": False,
                "selection_rule": "Rule 2: Secondary keyless commercial-safe stock match via Internet Archive",
                "relevance_score": "not_scored",
            }
    return None


def _load_unicode_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in [
        "C:/Windows/Fonts/Nirmala.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _wrap_lines(text: str, max_chars: int = 36) -> str:
    words = text.split()
    lines, cur = [], []
    for w in words:
        if sum(len(x) + 1 for x in cur) + len(w) > max_chars and cur:
            lines.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    return "\n".join(lines[:4])


def _generate_motion_card(text: str, index: int, duration: float, out_path: str):
    palette = [(22, 33, 62), (15, 52, 96), (26, 26, 46), (34, 40, 49)]
    bg = palette[index % len(palette)]
    img_path = out_path + ".png"
    img = Image.new("RGB", (1920, 1080), color=bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([140, 320, 1780, 760], outline=(0, 210, 255), width=4)
    font = _load_unicode_font(54)
    draw.multiline_text((190, 410), _wrap_lines(text, 38), fill=(255, 255, 255), font=font, spacing=18)
    img.save(img_path)

    frames = max(30, int(duration * 30))
    subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-i", img_path,
            "-vf", f"zoompan=z='min(zoom+0.001,1.12)':d={frames}:s=1920x1080:fps=30,format=yuv420p",
            "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path,
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.remove(img_path)