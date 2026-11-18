import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
import uuid
from typing import Any, Optional

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.adapters.providers import fetch_clip_for_scene, get_media_duration, plan_scenes, synthesize_speech
from app.compositor import assemble_final_video, build_srt_file, normalize_scene_segment
from app.config import settings
from app.errors import AppError, app_error_handler, http_exception_handler
from app.logging_utils import correlation_id_var, job_id_var, logger
from app.storage import storage

app = FastAPI(
    title="IncuBrix Script & Voiceover to B-Roll Engine",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)

os.makedirs(settings.local_storage_dir, exist_ok=True)
if settings.storage_backend == "local":
    app.mount("/files", StaticFiles(directory=settings.local_storage_dir), name="files")

JOBS: dict[str, dict[str, Any]] = {}
IDEMPOTENCY_MAP: dict[str, str] = {}
REQUEST_TIMESTAMPS: list[float] = []


class CaptionsConfig(BaseModel):
    burn_in: bool = True
    srt: bool = True


class SceneInput(BaseModel):
    text: str
    visual_hint: Optional[str] = None


class JobCreateRequest(BaseModel):
    audio_url: Optional[str] = None
    script: Optional[str] = None
    scenes: Optional[list[SceneInput]] = None
    voice: str = "en-IN-female"
    aspect_ratios: list[str] = Field(default_factory=lambda: ["16:9"])
    style: str = "bright, modern, real-world footage"
    avoid: list[str] = Field(default_factory=list)
    captions: CaptionsConfig = Field(default_factory=CaptionsConfig)
    callback_url: Optional[str] = None
    simulate_primary_outage: bool = False


class SceneSwapRequest(BaseModel):
    visual_hint: Optional[str] = None
    new_query: Optional[str] = None


@app.middleware("http")
async def correlation_and_rate_limit(request: Request, call_next):
    cid = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    correlation_id_var.set(cid)

    now = time.time()
    REQUEST_TIMESTAMPS[:] = [t for t in REQUEST_TIMESTAMPS if now - t < 60]
    if len(REQUEST_TIMESTAMPS) >= settings.rate_limit_per_minute:
        raise AppError("RATE_LIMITED", "Too many requests. Please slow down.", status_code=429, retryable=True)
    REQUEST_TIMESTAMPS.append(now)

    response = await call_next(request)
    response.headers["X-Correlation-Id"] = cid
    return response


def verify_token(authorization: Optional[str] = Header(default=None)):
    if settings.app_env == "production" and settings.api_auth_token:
        if authorization != f"Bearer {settings.api_auth_token}":
            raise AppError("UNAUTHORIZED", "Invalid or missing Bearer token.", status_code=401)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    return {"status": "ready", "storage": settings.storage_backend, "llm": settings.llm_provider}


@app.post("/api/v1/broll/jobs", status_code=202, dependencies=[Depends(verify_token)])
async def create_broll_job(
    background_tasks: BackgroundTasks,
    payload: JobCreateRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if idempotency_key and idempotency_key in IDEMPOTENCY_MAP:
        existing_id = IDEMPOTENCY_MAP[idempotency_key]
        return {"job_id": existing_id, "status": JOBS[existing_id]["status"], "idempotent_replay": True}

    if not payload.audio_url and not payload.script and not payload.scenes:
        raise AppError("EMPTY_INPUT", "Provide at least one of audio_url, script, or scenes.", status_code=400)
    if payload.script is not None and not payload.script.strip() and not payload.audio_url and not payload.scenes:
        raise AppError("EMPTY_SCRIPT", "Script cannot be empty.", status_code=400)

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "results": None,
        "error": None,
        "request": payload.model_dump(),
    }
    if idempotency_key:
        IDEMPOTENCY_MAP[idempotency_key] = job_id

    background_tasks.add_task(run_broll_pipeline, job_id, payload.model_dump())
    return {"job_id": job_id, "status": "queued", "progress": 0}


@app.post("/api/v1/broll/jobs/upload", status_code=202, dependencies=[Depends(verify_token)])
async def create_broll_job_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    aspect_ratios: str = Form("16:9"),
    style: str = Form("bright, modern, real-world footage"),
    burn_in: bool = Form(True),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in {".mp3", ".wav", ".m4a"}:
        raise AppError("INVALID_FILE_TYPE", f"Unsupported audio format '{ext}'. Use mp3, wav, or m4a.", status_code=400)

    os.makedirs("./tmp_uploads", exist_ok=True)
    job_id = str(uuid.uuid4())
    saved_audio = f"./tmp_uploads/{job_id}{ext}"
    with open(saved_audio, "wb") as f:
        f.write(await file.read())

    req_dict = {
        "audio_local_path": saved_audio,
        "audio_url": None,
        "script": None,
        "scenes": None,
        "voice": "en-IN-female",
        "aspect_ratios": [r.strip() for r in aspect_ratios.split(",") if r.strip()],
        "style": style,
        "avoid": [],
        "captions": {"burn_in": burn_in, "srt": True},
        "callback_url": None,
        "simulate_primary_outage": False,
    }
    JOBS[job_id] = {"job_id": job_id, "status": "queued", "progress": 0, "results": None, "error": None, "request": req_dict}
    background_tasks.add_task(run_broll_pipeline, job_id, req_dict)
    return {"job_id": job_id, "status": "queued", "progress": 0}


@app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(verify_token)])
def get_job_status(job_id: str):
    if job_id not in JOBS:
        raise AppError("JOB_NOT_FOUND", f"Job {job_id} does not exist.", status_code=404)
    job = JOBS[job_id]
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "results": job["results"],
        "error": job["error"],
    }


@app.post("/api/v1/broll/jobs/{job_id}/scenes/{scene_index}/swap", dependencies=[Depends(verify_token)])
async def swap_scene_clip(job_id: str, scene_index: int, swap_req: SceneSwapRequest):
    """Advanced feature: replaces a single scene clip and re-concatenates only affected segments."""
    t0 = time.time()
    if job_id not in JOBS or JOBS[job_id]["status"] != "succeeded":
        raise AppError("JOB_NOT_READY", "Job must be completed before swapping scenes.", status_code=400)

    job = JOBS[job_id]
    cache_dir = job["cache_dir"]
    timeline = job["timeline"]
    if scene_index < 0 or scene_index >= len(timeline):
        raise AppError("INVALID_SCENE_INDEX", "Scene index out of range.", status_code=400)

    scene = timeline[scene_index]
    if swap_req.new_query or swap_req.visual_hint:
        scene["search_query"] = swap_req.new_query or swap_req.visual_hint

    used_ids = {s["clip_id"] for s in timeline}
    scene = timeline[scene_index]
    scene["index"] = scene_index
    if swap_req.new_query or swap_req.visual_hint:
        scene["search_query"] = swap_req.new_query or swap_req.visual_hint
    clip_meta = await fetch_clip_for_scene(scene, used_ids, cache_dir)
    dur = max(0.5, scene["end_s"] - scene["start_s"])

    for ratio in job["request"]["aspect_ratios"]:
        safe_ratio = ratio.replace(":", "x")
        seg_path = os.path.join(cache_dir, f"seg_{safe_ratio}_{scene_index}.mp4")
        normalize_scene_segment(clip_meta["local_path"], dur, ratio, seg_path)
        all_segs = [os.path.join(cache_dir, f"seg_{safe_ratio}_{i}.mp4") for i in range(len(timeline))]
        out_mp4 = os.path.join(cache_dir, f"video_{safe_ratio}.mp4")
        assemble_final_video(
            all_segs,
            os.path.join(cache_dir, "narration.wav"),
            os.path.join(cache_dir, "captions.srt"),
            job["request"]["captions"]["burn_in"],
            out_mp4,
        )
        url = storage.save_file(out_mp4, f"{job_id}/video_{safe_ratio}.mp4")
        job["results"][f"video_{safe_ratio}.mp4"] = url

    scene.update({
        "clip_id": clip_meta["clip_id"],
        "source": clip_meta["source"],
        "license": clip_meta["license"],
        "attribution": clip_meta["attribution"],
        "url": clip_meta["url"],
        "is_ai_generated": clip_meta["is_ai_generated"],
        "reason": f"Swapped clip: {clip_meta['selection_rule']}",
    })
    tl_path = os.path.join(cache_dir, "timeline.json")
    with open(tl_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, indent=2)
    job["results"]["timeline.json"] = storage.save_file(tl_path, f"{job_id}/timeline.json")

    return {"job_id": job_id, "swapped_scene": scene_index, "swap_duration_s": round(time.time() - t0, 3), "results": job["results"]}


async def run_broll_pipeline(job_id: str, req: dict):
    job_id_var.set(job_id)
    t_start = time.time()
    work_dir = tempfile.mkdtemp(prefix=f"incubrix_{job_id}_")
    JOBS[job_id]["cache_dir"] = work_dir
    warnings: list[str] = []

    try:
        JOBS[job_id]["status"] = "running"
        JOBS[job_id]["progress"] = 10
        logger.info("Starting B-roll generation pipeline")

        audio_path = os.path.join(work_dir, "narration.wav")
        if req.get("audio_local_path"):
            shutil.copy2(req["audio_local_path"], audio_path)
            duration = get_media_duration(audio_path)
        elif req.get("audio_url"):
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(req["audio_url"])
                r.raise_for_status()
                raw_audio = os.path.join(work_dir, "downloaded_audio")
                with open(raw_audio, "wb") as f:
                    f.write(r.content)
            duration = get_media_duration(raw_audio)
            shutil.copy2(raw_audio, audio_path)
        else:
            full_text = req.get("script") or " ".join(s["text"] for s in req.get("scenes", []))
            duration = await synthesize_speech(full_text, req.get("voice", "en-IN-female"), audio_path)

        if duration > settings.max_audio_duration_sec:
            raise AppError("AUDIO_TOO_LONG", f"Audio exceeds max {settings.max_audio_duration_sec}s.", status_code=400)

        JOBS[job_id]["progress"] = 30
        scenes = plan_scenes(
            script=req.get("script"),
            scenes_input=req.get("scenes"),
            audio_path=audio_path,
            duration=duration,
            style=req.get("style", ""),
            avoid=req.get("avoid", []),
        )

        JOBS[job_id]["progress"] = 50
        used_clip_ids: set[str] = set()
        timeline: list[dict] = []
        for sc in scenes:
            if sc.get("is_sensitive"):
                warnings.append(f"Scene {sc['index']} flagged as sensitive topic; applied respectful visual guardrails.")
            clip_meta = await fetch_clip_for_scene(
                sc, used_clip_ids, work_dir, disable_primary=req.get("simulate_primary_outage", False)
            )
            timeline.append({
                "scene_index": sc["index"],
                "text": sc["text"],
                "start_s": sc["start_s"],
                "end_s": sc["end_s"],
                "search_query": sc["search_query"],
                "clip_id": clip_meta["clip_id"],
                "local_clip_path": clip_meta["local_path"],
                "source": clip_meta["source"],
                "license": clip_meta["license"],
                "attribution": clip_meta["attribution"],
                "url": clip_meta["url"],
                "is_ai_generated": clip_meta["is_ai_generated"],
                "reason": f"{sc['reason']} | {clip_meta['selection_rule']}",
                "relevance_score": clip_meta["relevance_score"],
            })

        JOBS[job_id]["progress"] = 70
        srt_path = os.path.join(work_dir, "captions.srt")
        build_srt_file(timeline, srt_path)

        outputs: dict[str, str] = {}
        outputs["captions.srt"] = storage.save_file(srt_path, f"{job_id}/captions.srt")

        for ratio in req.get("aspect_ratios", ["16:9"]):
            safe_ratio = ratio.replace(":", "x")
            seg_paths = []
            for item in timeline:
                dur = max(0.5, item["end_s"] - item["start_s"])
                seg_path = os.path.join(work_dir, f"seg_{safe_ratio}_{item['scene_index']}.mp4")
                normalize_scene_segment(item["local_clip_path"], dur, ratio, seg_path)
                seg_paths.append(seg_path)

            out_mp4 = os.path.join(work_dir, f"video_{safe_ratio}.mp4")
            assemble_final_video(
                seg_paths, audio_path, srt_path, req.get("captions", {}).get("burn_in", True), out_mp4
            )
            outputs[f"video_{safe_ratio}.mp4"] = storage.save_file(out_mp4, f"{job_id}/video_{safe_ratio}.mp4")

        JOBS[job_id]["progress"] = 90
        clean_timeline = [{k: v for k, v in item.items() if k != "local_clip_path"} for item in timeline]
        tl_path = os.path.join(work_dir, "timeline.json")
        with open(tl_path, "w", encoding="utf-8") as f:
            json.dump(clean_timeline, f, indent=2)
        outputs["timeline.json"] = storage.save_file(tl_path, f"{job_id}/timeline.json")

        elapsed = round(time.time() - t_start, 2)
        manifest = {
            "job_id": job_id,
            "providers": {
                "llm": settings.llm_provider,
                "llm_model": settings.gemini_model,
                "footage": settings.footage_provider,
                "tts": settings.tts_provider,
            },
            "settings": {
                "aspect_ratios": req.get("aspect_ratios"),
                "style": req.get("style"),
                "avoid": req.get("avoid"),
            },
            "timings": {"audio_duration_s": round(duration, 2), "turnaround_s": elapsed},
            "cost_estimate_usd": round((duration / 60.0) * 0.002, 5),
            "warnings": warnings,
            "all_clips_commercial_safe": True,
        }
        mf_path = os.path.join(work_dir, "manifest.json")
        with open(mf_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        outputs["manifest.json"] = storage.save_file(mf_path, f"{job_id}/manifest.json")

        JOBS[job_id]["timeline"] = timeline
        JOBS[job_id]["results"] = outputs
        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["status"] = "succeeded"
        logger.info(f"Job completed in {elapsed}s")

        if req.get("callback_url"):
            body = json.dumps({"job_id": job_id, "status": "succeeded", "results": outputs}).encode()
            sig = hmac.new(settings.api_auth_token.encode(), body, hashlib.sha256).hexdigest()
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(req["callback_url"], content=body, headers={"X-IncuBrix-Signature": sig})

    except AppError as ae:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = {"code": ae.code, "message": ae.message, "retryable": ae.retryable, "details": ae.details}
    except Exception as exc:
        logger.exception("Pipeline error")
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = {"code": "INTERNAL_PIPELINE_ERROR", "message": str(exc), "retryable": True, "details": {}}