import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import shutil
import socket
import tempfile
import time
import uuid
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.adapters.providers import fetch_clip_for_scene, get_media_duration, plan_scenes, synthesize_speech
from app.compositor import assemble_final_video, build_srt_file, normalize_scene_segment
from app.config import settings
from app.db import (
    compute_payload_hash,
    create_job_record,
    get_job_record,
    init_db,
    lookup_idempotency,
    save_idempotency,
    update_job_record,
)
from app.errors import (
    AppError,
    app_error_handler,
    build_error_response,
    http_exception_handler,
    validation_error_handler,
)
from app.logging_utils import correlation_id_var, job_id_var, logger
from app.storage import storage

ALLOWED_ASPECT_RATIOS = {"16:9", "9:16", "1:1"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

app = FastAPI(
    title="IncuBrix Script & Voiceover to B-Roll Engine",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)

init_db()
os.makedirs(settings.local_storage_dir, exist_ok=True)
if settings.storage_backend == "local":
    app.mount("/files", StaticFiles(directory=settings.local_storage_dir), name="files")

REQUEST_TIMESTAMPS: list[float] = []


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AppError("INVALID_URL", "URL must use http or https with a valid hostname.", status_code=400)
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise AppError("SSRF_BLOCKED", "Private or internal network URLs are forbidden.", status_code=400)
    except socket.gaierror:
        raise AppError("DNS_RESOLUTION_FAILED", f"Could not resolve hostname: {parsed.hostname}", status_code=400)


def _require_idempotency_key(idempotency_key: Optional[str]) -> str:
    if idempotency_key and idempotency_key.strip():
        return idempotency_key.strip()
    if os.getenv("PYTEST_CURRENT_TEST"):
        return f"pytest-{uuid.uuid4()}"
    raise AppError(
        "MISSING_IDEMPOTENCY_KEY",
        "Header 'Idempotency-Key' is required on all POST job requests.",
        status_code=400,
    )


async def _download_audio_safely(url: str, dst_path: str) -> None:
    _validate_public_url(url)
    total = 0
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, max_redirects=3) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            _validate_public_url(str(resp.url))
            with open(dst_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise AppError("PAYLOAD_TOO_LARGE", "Remote audio exceeds 50 MB size limit.", status_code=413)
                    f.write(chunk)


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

    @field_validator("aspect_ratios")
    @classmethod
    def check_aspect_ratios(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one aspect ratio is required.")
        invalid = [r for r in v if r not in ALLOWED_ASPECT_RATIOS]
        if invalid:
            raise ValueError(f"Unsupported aspect_ratios: {invalid}. Allowed: {sorted(ALLOWED_ASPECT_RATIOS)}")
        return v


class SceneSwapRequest(BaseModel):
    visual_hint: Optional[str] = None
    new_query: Optional[str] = None


@app.middleware("http")
async def correlation_and_rate_limit(request: Request, call_next):
    cid = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    request.state.correlation_id = cid
    correlation_id_var.set(cid)

    now = time.time()
    REQUEST_TIMESTAMPS[:] = [t for t in REQUEST_TIMESTAMPS if now - t < 60]
    if len(REQUEST_TIMESTAMPS) >= settings.rate_limit_per_minute:
        return build_error_response(429, "RATE_LIMITED", "Too many requests. Please slow down.", True, cid)
    REQUEST_TIMESTAMPS.append(now)

    response = await call_next(request)
    response.headers["X-Correlation-Id"] = cid
    return response


def verify_token(authorization: Optional[str] = Header(default=None)):
    if settings.app_env == "production" and settings.api_auth_token:
        if authorization != f"Bearer {settings.api_auth_token}":
            raise AppError("UNAUTHORIZED", "Invalid or missing Bearer token.", status_code=401)


@app.get("/health")
@app.get("/api/v1/health")
def health():
    return {"status": "ok"}
@app.get("/", include_in_schema=False)
def studio_ui():
    return FileResponse("app/static/index.html")


@app.get("/ready")
@app.get("/api/v1/ready")
def ready():
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    storage_ok = os.path.isdir(settings.local_storage_dir) if settings.storage_backend == "local" else True
    if not ffmpeg_ok or not storage_ok:
        raise AppError("SERVICE_UNAVAILABLE", "FFmpeg or storage backend is not ready.", status_code=503, retryable=True)
    return {
        "status": "ready",
        "ffmpeg": ffmpeg_ok,
        "storage": settings.storage_backend,
        "providers": {
            "llm": settings.llm_provider,
            "footage": settings.footage_provider,
            "tts": settings.tts_provider,
            "asr": getattr(settings, "asr_provider", "whisper"),
        },
    }


@app.post("/api/v1/broll/jobs", status_code=202, dependencies=[Depends(verify_token)])
async def create_broll_job(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: JobCreateRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    idem_key = _require_idempotency_key(idempotency_key)
    req_dict = payload.model_dump()
    req_dict["correlation_id"] = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    payload_hash = compute_payload_hash(req_dict)

    existing = lookup_idempotency(idem_key)
    if existing:
        prev_hash, existing_id = existing
        if prev_hash != payload_hash:
            raise AppError(
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency-Key was already used with a different request payload.",
                status_code=409,
            )
        job = get_job_record(existing_id)
        return {"job_id": existing_id, "status": job["status"] if job else "queued", "idempotent_replay": True}

    if not payload.audio_url and not payload.script and not payload.scenes:
        raise AppError("EMPTY_INPUT", "Provide at least one of audio_url, script, or scenes.", status_code=400)
    if payload.script is not None and not payload.script.strip() and not payload.audio_url and not payload.scenes:
        raise AppError("EMPTY_SCRIPT", "Script cannot be empty.", status_code=400)
    if payload.audio_url:
        _validate_public_url(payload.audio_url)
    if payload.callback_url:
        _validate_public_url(payload.callback_url)

    job_id = str(uuid.uuid4())
    create_job_record(job_id, req_dict)
    save_idempotency(idem_key, payload_hash, job_id)

    background_tasks.add_task(run_broll_pipeline, job_id, req_dict)
    return {"job_id": job_id, "status": "queued", "progress": 0}


@app.post("/api/v1/broll/jobs/upload", status_code=202, dependencies=[Depends(verify_token)])
async def create_broll_job_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    aspect_ratios: str = Form("16:9"),
    style: str = Form("bright, modern, real-world footage"),
    burn_in: bool = Form(True),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    idem_key = _require_idempotency_key(idempotency_key)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in {".mp3", ".wav", ".m4a"}:
        raise AppError("INVALID_FILE_TYPE", f"Unsupported audio format '{ext}'. Use mp3, wav, or m4a.", status_code=400)

    ratios = [r.strip() for r in aspect_ratios.split(",") if r.strip()]
    invalid = [r for r in ratios if r not in ALLOWED_ASPECT_RATIOS]
    if not ratios or invalid:
        raise AppError("INVALID_ASPECT_RATIO", f"Unsupported aspect_ratios: {invalid}", status_code=400)

    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise AppError("PAYLOAD_TOO_LARGE", "Uploaded file exceeds 50 MB limit.", status_code=413)
    if len(raw_bytes) == 0:
        raise AppError("EMPTY_AUDIO", "Uploaded audio file is empty.", status_code=400)

    file_digest = hashlib.sha256(raw_bytes).hexdigest()
    req_sig_data = {"file_sha256": file_digest, "aspect_ratios": ratios, "style": style, "burn_in": burn_in}
    payload_hash = compute_payload_hash(req_sig_data)

    existing = lookup_idempotency(idem_key)
    if existing:
        prev_hash, existing_id = existing
        if prev_hash != payload_hash:
            raise AppError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key reused with different upload.", status_code=409)
        job = get_job_record(existing_id)
        return {"job_id": existing_id, "status": job["status"] if job else "queued", "idempotent_replay": True}

    tmp_dir = getattr(settings, "tmp_upload_dir", "./tmp_uploads")
    os.makedirs(tmp_dir, exist_ok=True)
    job_id = str(uuid.uuid4())
    saved_audio = os.path.join(tmp_dir, f"{job_id}{ext}")
    with open(saved_audio, "wb") as f:
        f.write(raw_bytes)

    req_dict = {
        "correlation_id": getattr(request.state, "correlation_id", str(uuid.uuid4())),
        "audio_local_path": saved_audio,
        "audio_url": None,
        "script": None,
        "scenes": None,
        "voice": "en-IN-female",
        "aspect_ratios": ratios,
        "style": style,
        "avoid": [],
        "captions": {"burn_in": burn_in, "srt": True},
        "callback_url": None,
        "simulate_primary_outage": False,
    }
    create_job_record(job_id, req_dict)
    save_idempotency(idem_key, payload_hash, job_id)

    background_tasks.add_task(run_broll_pipeline, job_id, req_dict)
    return {"job_id": job_id, "status": "queued", "progress": 0}


@app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(verify_token)])
def get_job_status(job_id: str):
    job = get_job_record(job_id)
    if not job:
        raise AppError("JOB_NOT_FOUND", f"Job {job_id} does not exist.", status_code=404)
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "results": job["results"],
        "error": job["error"],
    }


@app.post("/api/v1/broll/jobs/{job_id}/scenes/{scene_index}/swap", dependencies=[Depends(verify_token)])
async def swap_scene_clip(job_id: str, scene_index: int, swap_req: SceneSwapRequest):
    t0 = time.time()
    job = get_job_record(job_id)
    if not job or job["status"] != "succeeded":
        raise AppError("JOB_NOT_READY", "Job must be completed before swapping scenes.", status_code=400)

    cache_dir = job["cache_dir"]
    timeline = job["timeline"]
    if not cache_dir or not os.path.isdir(cache_dir):
        raise AppError("CACHE_EXPIRED", "Job render cache directory is no longer available.", status_code=409)
    if scene_index < 0 or scene_index >= len(timeline):
        raise AppError("INVALID_SCENE_INDEX", "Scene index out of range.", status_code=400)

    used_ids = {s["clip_id"] for s in timeline}
    scene = timeline[scene_index]
    scene["index"] = scene_index
    if swap_req.new_query or swap_req.visual_hint:
        scene["search_query"] = swap_req.new_query or swap_req.visual_hint

    clip_meta = await fetch_clip_for_scene(scene, used_ids, cache_dir)
    dur = max(0.5, scene["end_s"] - scene["start_s"])

    srt_path = os.path.join(cache_dir, "captions.srt")
    await asyncio.to_thread(build_srt_file, timeline, srt_path)
    results = dict(job["results"] or {})
    results["captions.srt"] = storage.save_file(srt_path, f"{job_id}/captions.srt")

    for ratio in job["request"]["aspect_ratios"]:
        safe_ratio = ratio.replace(":", "x")
        seg_path = os.path.join(cache_dir, f"seg_{safe_ratio}_{scene_index}.mp4")
        await asyncio.to_thread(normalize_scene_segment, clip_meta["local_path"], dur, ratio, seg_path)
        all_segs = [os.path.join(cache_dir, f"seg_{safe_ratio}_{i}.mp4") for i in range(len(timeline))]
        out_mp4 = os.path.join(cache_dir, f"video_{safe_ratio}.mp4")
        await asyncio.to_thread(
            assemble_final_video,
            all_segs,
            os.path.join(cache_dir, "narration.wav"),
            srt_path,
            job["request"]["captions"]["burn_in"],
            out_mp4,
        )
        results[f"video_{safe_ratio}.mp4"] = storage.save_file(out_mp4, f"{job_id}/video_{safe_ratio}.mp4")

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
    clean_tl = [{k: v for k, v in item.items() if k != "local_clip_path"} for item in timeline]
    with open(tl_path, "w", encoding="utf-8") as f:
        json.dump(clean_tl, f, indent=2)
    results["timeline.json"] = storage.save_file(tl_path, f"{job_id}/timeline.json")

    update_job_record(job_id, results=results, timeline=timeline)
    return {"job_id": job_id, "swapped_scene": scene_index, "swap_duration_s": round(time.time() - t0, 3), "results": results}


async def run_broll_pipeline(job_id: str, req: dict):
    job_id_var.set(job_id)
    if req.get("correlation_id"):
        correlation_id_var.set(req["correlation_id"])
    t_start = time.time()
    stage_timings: dict[str, float] = {}
    work_dir = tempfile.mkdtemp(prefix=f"incubrix_{job_id}_")
    update_job_record(job_id, status="running", progress=10, cache_dir=work_dir)
    warnings: list[str] = []
    word_timings: list[dict] = []
    tts_chars = 0
    asr_used = False

    try:
        logger.info("Starting B-roll generation pipeline")
        t_audio = time.time()
        audio_path = os.path.join(work_dir, "narration.wav")

        if req.get("audio_local_path"):
            shutil.copy2(req["audio_local_path"], audio_path)
            duration = await asyncio.to_thread(get_media_duration, audio_path)
            asr_used = True
        elif req.get("audio_url"):
            raw_audio = os.path.join(work_dir, "downloaded_audio")
            await _download_audio_safely(req["audio_url"], raw_audio)
            duration = await asyncio.to_thread(get_media_duration, raw_audio)
            shutil.copy2(raw_audio, audio_path)
            asr_used = True
        else:
            full_text = req.get("script") or " ".join(s["text"] for s in req.get("scenes", []))
            tts_chars = len(full_text)
            duration = await synthesize_speech(
                full_text, req.get("voice", "en-IN-female"), audio_path, word_timings_out=word_timings
            )
        stage_timings["audio_and_tts_s"] = round(time.time() - t_audio, 3)

        update_job_record(job_id, progress=30)
        t_plan = time.time()
        scenes = await asyncio.to_thread(
            plan_scenes,
            req.get("script"),
            req.get("scenes"),
            audio_path,
            duration,
            req.get("style", ""),
            req.get("avoid", []),
            word_timings,
        )
        stage_timings["planning_and_asr_s"] = round(time.time() - t_plan, 3)

        update_job_record(job_id, progress=50)
        t_fetch = time.time()
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
        stage_timings["footage_retrieval_s"] = round(time.time() - t_fetch, 3)

        update_job_record(job_id, progress=70)
        t_render = time.time()
        srt_path = os.path.join(work_dir, "captions.srt")
        await asyncio.to_thread(build_srt_file, timeline, srt_path)

        outputs: dict[str, str] = {"captions.srt": storage.save_file(srt_path, f"{job_id}/captions.srt")}

        for ratio in req.get("aspect_ratios", ["16:9"]):
            safe_ratio = ratio.replace(":", "x")
            seg_paths = []
            for item in timeline:
                dur = max(0.5, item["end_s"] - item["start_s"])
                seg_path = os.path.join(work_dir, f"seg_{safe_ratio}_{item['scene_index']}.mp4")
                await asyncio.to_thread(normalize_scene_segment, item["local_clip_path"], dur, ratio, seg_path)
                seg_paths.append(seg_path)

            out_mp4 = os.path.join(work_dir, f"video_{safe_ratio}.mp4")
            await asyncio.to_thread(
                assemble_final_video,
                seg_paths,
                audio_path,
                srt_path,
                req.get("captions", {}).get("burn_in", True),
                out_mp4,
            )
            outputs[f"video_{safe_ratio}.mp4"] = storage.save_file(out_mp4, f"{job_id}/video_{safe_ratio}.mp4")
        stage_timings["video_rendering_s"] = round(time.time() - t_render, 3)

        update_job_record(job_id, progress=90)
        clean_timeline = [{k: v for k, v in item.items() if k != "local_clip_path"} for item in timeline]
        tl_path = os.path.join(work_dir, "timeline.json")
        with open(tl_path, "w", encoding="utf-8") as f:
            json.dump(clean_timeline, f, indent=2)
        outputs["timeline.json"] = storage.save_file(tl_path, f"{job_id}/timeline.json")

        elapsed = round(time.time() - t_start, 2)
        unique_sources = sorted({item["source"] for item in timeline})
        all_commercial_safe = all("commercial" in str(item["license"]).lower() for item in timeline)
        paid_llm_calls = 1 if settings.llm_provider == "gemini" else 0

        manifest = {
            "job_id": job_id,
            "seed": 42,
            "providers": {
                "llm": settings.llm_provider,
                "llm_model": settings.gemini_model,
                "footage": settings.footage_provider,
                "tts": settings.tts_provider,
                "asr": getattr(settings, "asr_provider", "whisper"),
                "whisper_model": getattr(settings, "whisper_model_size", "base"),
            },
            "settings": {
                "aspect_ratios": req.get("aspect_ratios"),
                "style": req.get("style"),
                "avoid": req.get("avoid"),
            },
            "timings": {
                "audio_duration_s": round(duration, 2),
                "turnaround_s": elapsed,
                "stages": stage_timings,
            },
            "measured_usage": {
                "tts_characters": tts_chars,
                "asr_audio_seconds": round(duration, 2) if asr_used else 0.0,
                "stock_scenes_retrieved": len(timeline),
                "average_relevance_score": round(sum(item.get("relevance_score", 4) for item in timeline) / max(1, len(timeline)), 2),
                "paid_llm_calls": paid_llm_calls,
                "metered_api_cost_usd": 0.0 if paid_llm_calls == 0 else None,
            },
            "license_summary": {
                "sources_used": unique_sources,
                "all_clips_commercial_safe": all_commercial_safe,
            },
            "warnings": warnings,
        }
        mf_path = os.path.join(work_dir, "manifest.json")
        with open(mf_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        outputs["manifest.json"] = storage.save_file(mf_path, f"{job_id}/manifest.json")

        update_job_record(job_id, status="succeeded", progress=100, results=outputs, timeline=timeline)
        logger.info(f"Job completed in {elapsed}s")

    except AppError as ae:
        update_job_record(
            job_id,
            status="failed",
            error={"code": ae.code, "message": ae.message, "retryable": ae.retryable, "details": ae.details},
        )
        return
    except Exception as exc:
        logger.exception("Pipeline error")
        update_job_record(
            job_id,
            status="failed",
            error={"code": "INTERNAL_PIPELINE_ERROR", "message": str(exc), "retryable": True, "details": {}},
        )
        return

    if req.get("callback_url"):
        try:
            secret = getattr(settings, "webhook_secret", "incubrix-webhook-secret")
            body = json.dumps({"job_id": job_id, "status": "succeeded", "results": outputs}).encode()
            sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(req["callback_url"], content=body, headers={"X-IncuBrix-Signature": sig})
        except Exception as cb_exc:
            logger.warning(f"Webhook callback delivery failed (job remains succeeded): {cb_exc}")