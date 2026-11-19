import argparse
import json
import os
import time
import httpx


def run_batch(base_url: str, inputs_path: str, out_dir: str, token: str):
    os.makedirs(out_dir, exist_ok=True)
    with open(inputs_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    summary = []

    with httpx.Client(timeout=60.0) as client:
        for item in cases:
            name = item["name"]
            payload = item["payload"]
            t0 = time.time()

            resp = client.post(f"{base_url}/api/v1/broll/jobs", json=payload, headers=headers)
            if resp.status_code != 202:
                elapsed = round(time.time() - t0, 2)
                print(f"[{name}] rejected ({resp.status_code}) in {elapsed}s -> {resp.text}")
                summary.append({"name": name, "status": f"rejected_{resp.status_code}", "elapsed_s": elapsed, "response": resp.json()})
                continue

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
                    summary.append({
                        "name": name,
                        "job_id": job_id,
                        "status": st["status"],
                        "elapsed_s": elapsed,
                        "results": st.get("results"),
                        "error": st.get("error"),
                    })
                    break

    with open(os.path.join(out_dir, "batch_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\nBatch complete. Summary saved to {out_dir}/batch_summary.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--inputs", default="test_inputs.json")
    parser.add_argument("--out", default="batch_results")
    parser.add_argument("--token", default="incubrix-demo-token")
    args = parser.parse_args()
    run_batch(args.url, args.inputs, args.out, args.token)