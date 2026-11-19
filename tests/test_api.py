import os
os.environ["TTS_PROVIDER"] = "mock"
os.environ["FOOTAGE_PROVIDER"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["ASR_PROVIDER"] = "mock"
import time
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_and_ready():
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_empty_script_error_format():
    resp = client.post("/api/v1/broll/jobs", json={"script": "   "})
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "EMPTY_SCRIPT"
    assert body["error"]["retryable"] is False


def test_idempotency_key_returns_same_job():
    payload = {"script": "Cloud computing scales fast. Sales grew 40% in 2025.", "aspect_ratios": ["16:9"]}
    r1 = client.post("/api/v1/broll/jobs", json=payload, headers={"Idempotency-Key": "idem-test-1"})
    r2 = client.post("/api/v1/broll/jobs", json=payload, headers={"Idempotency-Key": "idem-test-1"})
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["job_id"] == r2.json()["job_id"]


def test_end_to_end_generation_and_scene_swap():
    payload = {
        "script": "Cloud computing transforms teams. Trust is earned slowly.",
        "aspect_ratios": ["16:9", "9:16"],
        "captions": {"burn_in": True, "srt": True},
    }
    create = client.post("/api/v1/broll/jobs", json=payload)
    assert create.status_code == 202
    job_id = create.json()["job_id"]

    for _ in range(40):
        status_resp = client.get(f"/api/v1/jobs/{job_id}").json()
        if status_resp["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.25)

    assert status_resp["status"] == "succeeded"
    results = status_resp["results"]
    assert "video_16x9.mp4" in results
    assert "video_9x16.mp4" in results
    assert "captions.srt" in results
    assert "timeline.json" in results
    assert "manifest.json" in results

    # Test Advanced Scene Swap
    swap = client.post(
        f"/api/v1/broll/jobs/{job_id}/scenes/0/swap",
        json={"new_query": "modern office collaboration"},
    )
    assert swap.status_code == 200
    assert swap.json()["swapped_scene"] == 0