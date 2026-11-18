import json
import os
import re
import subprocess
import httpx
import edge_tts
from PIL import Image, ImageDraw
from google import genai
from app.config import settings
from app.errors import AppError
from app.logging_utils import logger


# 1. TTS ADAPTER (Text -> Audio)
async def synthesize_speech(text: str, voice: str, out_path: str) -> float:
    if not text or not text.strip():
        raise AppError("EMPTY_SCRIPT", "Input script cannot be empty.", status_code=400)

    if settings.tts_provider == "mock" or not text.strip():
        duration = max(3.0, min(15.0, len(text.split()) * 0.4))
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
                "-af", "volume=0.05", "-ar", "44100", "-ac", "2", out_path,
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return duration

    voice_map = {
        "en-IN-female": "en-IN-NeerjaNeural",
        "en-IN-male": "en-IN-PrabhatNeural",
        "hi-IN-female": "hi-IN-SwaraNeural",
        "ta-IN-female": "ta-IN-PallaviNeural",
    }
    selected_voice = voice_map.get(voice, voice if "-" in voice else "en-IN-NeerjaNeural")
    mp3_tmp = out_path + ".mp3"
    communicate = edge_tts.Communicate(text, selected_voice)
    await communicate.save(mp3_tmp)
    subprocess.run(
        ["ffmpeg", "-y", "-i", mp3_tmp, "-ar", "44100", "-ac", "2", out_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.remove(mp3_tmp)
    return get_media_duration(out_path)


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
    return float(res.stdout.strip())


# 2. SEMANTIC SCENE PLANNER & TRANSCRIBER (Solves keyword traps, hard cases & timing)
def plan_scenes(
    script: str | None,
    scenes_input: list[dict] | None,
    audio_path: str,
    duration: float,
    style: str,
    avoid: list[str],
) -> list[dict]:
    if duration <= 0.2:
        raise AppError("INVALID_AUDIO", "Audio file has zero duration.", status_code=400)

    # Case A: Pre-supplied IncuBrix scene list
    if scenes_input:
        total_words = sum(max(1, len(s["text"].split())) for s in scenes_input)
        cur = 0.0
        planned = []
        for idx, item in enumerate(scenes_input):
            share = max(1, len(item["text"].split())) / total_words
            seg_dur = duration - cur if idx == len(scenes_input) - 1 else round(duration * share, 2)
            planned.append({
                "index": idx,
                "text": item["text"],
                "start_s": round(cur, 2),
                "end_s": round(cur + seg_dur, 2),
                "search_query": item.get("visual_hint") or item["text"],
                "scene_type": "literal",
                "is_sensitive": False,
                "reason": "Matched user-provided visual_hint",
            })
            cur += seg_dur
        return planned

    # Case B: Offline Mock Mode (Deterministic sentence split, zero API keys)
    if settings.llm_provider == "mock" or not settings.gemini_api_key:
        text = (script or "Ambient background scene.").strip()
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        if not sentences:
            sentences = ["Ambient background scene."]
        total_words = sum(max(1, len(s.split())) for s in sentences)
        cur = 0.0
        planned = []
        for idx, sent in enumerate(sentences):
            share = max(1, len(sent.split())) / total_words
            seg_dur = duration - cur if idx == len(sentences) - 1 else round(duration * share, 2)
            query = _resolve_double_meanings_locally(sent)
            is_abstract = bool(re.search(r"\d+%|\btrust\b|\bstrategy\b|\bgrowth\b", sent.lower()))
            planned.append({
                "index": idx,
                "text": sent,
                "start_s": round(cur, 2),
                "end_s": round(cur + seg_dur, 2),
                "search_query": query,
                "scene_type": "abstract_or_stat" if is_abstract else "concrete",
                "is_sensitive": any(w in sent.lower() for w in ["tragedy", "died", "crash", "sad", "memorial"]),
                "reason": f"Semantic query '{query}' derived to avoid literal traps and respect avoid={avoid}",
            })
            cur += seg_dur
        return planned

    # Case C: Live Gemini 2.5 Flash Multimodal Audio/Script Planner
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = f"""
You are a video B-roll planner. Total audio duration is {duration:.2f} seconds.
Style: {style}. Avoid: {avoid}.
Script (if provided): {script or "Transcribe directly from the attached audio."}

Rules:
1. Break the narration into scenes strictly on sentence/phrase boundaries covering 0.0 to {duration:.2f}s with NO gaps.
2. Minimum scene length: 2.0s (merge short phrases if needed).
3. Resolve double-meaning words by context (e.g., "cloud computing" -> "server room data center", NOT sky; "value" -> "business growth chart", NOT piles of cash; "python" -> "software code screen", NOT snake).
4. If a real person or brand is named, DO NOT search for their face or logo; use safe editorial/contextual visuals.
5. For sad/sensitive news, choose respectful, somber symbolic visuals.
6. Set scene_type to "abstract_or_stat" if the line focuses on numbers/percentages/abstract concepts, else "concrete".
7. If audio has no speech (music/silence), create 4-second ambient scenes matching the style.

Return ONLY a valid JSON array of objects:
[{{"text": "...", "start_s": 0.0, "end_s": 3.5, "search_query": "concrete 3-5 word visual search", "scene_type": "concrete", "is_sensitive": false, "reason": "why this visual fits"}}]
"""
    contents = [prompt]
    if os.path.exists(audio_path):
        uploaded = client.files.upload(file=audio_path)
        contents.append(uploaded)

    resp = client.models.generate_content(model=settings.gemini_model, contents=contents)
    raw = resp.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    scenes = json.loads(raw)
    for idx, s in enumerate(scenes):
        s["index"] = idx
    scenes[0]["start_s"] = 0.0
    scenes[-1]["end_s"] = round(duration, 2)
    return scenes


def _resolve_double_meanings_locally(sentence: str) -> str:
    low = sentence.lower()
    if "cloud" in low and any(w in low for w in ["data", "computing", "server", "app", "software"]):
        return "server room data center technology"
    if "value" in low or "saving money" in low:
        return "financial planning laptop chart"
    if "python" in low and any(w in low for w in ["code", "script", "program", "ai"]):
        return "programmer typing code screen"
    if "bank" in low and any(w in low for w in ["river", "water", "stream"]):
        return "peaceful river shoreline nature"
    words = [w for w in re.findall(r"[a-zA-Z]+", low) if len(w) > 3][:4]
    return " ".join(words) or "modern workspace"


# 3. FOOTAGE PROVIDERS (Dual Source + Measurable Rule + Resilient Fallback)
async def fetch_clip_for_scene(
    scene: dict,
    used_clip_ids: set[str],
    work_dir: str,
    disable_primary: bool = False,
) -> dict:
    out_clip = os.path.join(work_dir, f"raw_scene_{scene['index']}.mp4")

    if (
        settings.footage_provider == "real"
        and settings.pixabay_api_key
        and not disable_primary
        and scene.get("scene_type") != "abstract_or_stat"
    ):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    "[https://pixabay.com/api/videos/](https://pixabay.com/api/videos/)",
                    params={
                        "key": settings.pixabay_api_key,
                        "q": scene["search_query"],
                        "per_page": 10,
                        "safesearch": "true",
                    },
                )
                r.raise_for_status()
                hits = r.json().get("hits", [])
                for hit in hits:
                    cid = f"pixabay:{hit['id']}"
                    if cid in used_clip_ids:
                        continue
                    video_url = hit["videos"]["medium"]["url"]
                    vid_resp = await client.get(video_url)
                    vid_resp.raise_for_status()
                    with open(out_clip, "wb") as f:
                        f.write(vid_resp.content)
                    used_clip_ids.add(cid)
                    return {
                        "local_path": out_clip,
                        "clip_id": cid,
                        "source": "Pixabay",
                        "license": "Pixabay Content License (Commercial Use Allowed, No Attribution Required)",
                        "attribution": f"Video by {hit.get('user', 'Pixabay Creator')} on Pixabay",
                        "url": hit.get("pageURL", "[https://pixabay.com](https://pixabay.com)"),
                        "is_ai_generated": False,
                        "selection_rule": "Rule 2: Concrete scene matched via Pixabay stock API",
                        "relevance_score": 4.5,
                    }
        except Exception as exc:
            logger.warning(f"Primary footage source failed, failing over to fallback: {exc}")

    # Source B: Deterministic Kinetic Motion Card Generator (Fallback & Abstract/Stat Scenes)
    cid = f"generated_card:{scene['index']}"
    used_clip_ids.add(cid)
    _generate_motion_card(scene["text"], scene["index"], max(1.0, scene["end_s"] - scene["start_s"]), out_clip)
    return {
        "local_path": out_clip,
        "clip_id": cid,
        "source": "IncuBrix Kinetic Motion Generator (Local Fallback)",
        "license": "Original Generated Asset (100% Commercial Use)",
        "attribution": "Generated locally via Pillow + FFmpeg",
        "url": "local://kinetic-generator",
        "is_ai_generated": True,
        "selection_rule": "Rule 1: Abstract/stat scene or primary provider unavailable -> Kinetic Motion Fallback",
        "relevance_score": 4.0,
    }


def _generate_motion_card(text: str, index: int, duration: float, out_path: str):
    palette = [(22, 33, 62), (15, 52, 96), (26, 26, 46), (34, 40, 49)]
    bg = palette[index % len(palette)]
    img_path = out_path + ".png"
    img = Image.new("RGB", (1920, 1080), color=bg)
    draw = ImageDraw.Draw(img)
    display_text = (text[:90] + "...") if len(text) > 90 else text
    draw.rectangle([140, 380, 1780, 700], outline=(0, 210, 255), width=4)
    draw.text((180, 510), display_text, fill=(255, 255, 255))
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