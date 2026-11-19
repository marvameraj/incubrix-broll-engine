import argparse
import json
import os
import time
from pathlib import Path
import httpx


def run_batch(base_url: str, inputs_path: str, out_dir: str, token: str):
    os.makedirs(out_dir, exist_ok=True)
    p = Path(inputs_path)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    summary = []

    with httpx.Client(timeout=60.0) as client:
        if p.is_dir():
            audio_files = sorted(f for f in p.iterdir() if f.suffix.lower() in {".mp3", ".wav", ".m4a"})
            for af in audio_files:
                name = af.stem
                t0 = time.time()
                req_headers = {**headers, "Idempotency-Key": f"batch-dir-{name}-{int(t0)}"}
                with open(af, "rb") as fh:
                    resp = client.post(
                        f"{base_url}/api/v1/broll/jobs/upload",
                        files={"file": (af.name, fh, "application/octet-stream")},
                        data={"aspect_ratios": "16:9"},
                        headers=req_headers,
                    )
                _poll_and_record(client, base_url, name, resp, t0, headers, summary)
        else:
            cases = json.loads(p.read_text(encoding="utf-8"))
            for item in cases:
                name = item["name"]
                payload = item["payload"]
                t0 = time.time()
                req_headers = {**headers, "Idempotency-Key": f"batch-json-{name}-{int(t0)}"}
                resp = client.post(f"{base_url}/api/v1/broll/jobs", json=payload, headers=req_headers)
                _poll_and_record(client, base_url, name, resp, t0, headers, summary)

    with open(os.path.join(out_dir, "batch_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\nBatch complete ({len(summary)} cases). Summary saved to {out_dir}/batch_summary.json")


def _poll_and_record(client, base_url, name, resp, t0, headers, summary):
    if resp.status_code != 202:
        elapsed = round(time.time() - t0, 2)
        print(f"[{name}] rejected ({resp.status_code}) in {elapsed}s -> {resp.text}")
        summary.append({"name": name, "status": f"rejected_{resp.status_code}", "elapsed_s": elapsed, "response": resp.json()})
        return
    job_id = resp.json()["job_id"]
    while True:
        time.sleep(2.0)
        st_resp = client.get(f"{base_url}/api/v1/jobs/{job_id}", headers=headers)
        if st_resp.status_code == 429:
            time.sleep(5.0)
            continue
        st = st_resp.json()
        if st.get("status") in {"succeeded", "failed"}:
            elapsed = round(time.time() - t0, 2)
            print(f"[{name}] {st['status']} in {elapsed}s -> {st.get('results') or st.get('error')}")
            summary.append({"name": name, "job_id": job_id, "status": st["status"], "elapsed_s": elapsed, "results": st.get("results"), "error": st.get("error")})
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="[http://127.0.0.1:8000](http://127.0.0.1:8000)")
    parser.add_argument("--inputs", default="test_inputs.json", help="Path to JSON file or directory of audio files")
    parser.add_argument("--out", default="batch_results")
    parser.add_argument("--token", default="incubrix-demo-token")
    args = parser.parse_args()
    run_batch(args.url, args.inputs, args.out, args.token)