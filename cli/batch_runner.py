import argparse
import json
import os
import time
import httpx


def run_batch(base_url: str, inputs_file: str, out_dir: str, token: str):
    os.makedirs(out_dir, exist_ok=True)
    with open(inputs_file, "r", encoding="utf-8") as f:
        items = json.load(f)

    headers = {"Authorization": f"Bearer {token}"}
    summary = []

    with httpx.Client(timeout=120.0) as client:
        for idx, item in enumerate(items):
            name = item.get("name", f"case_{idx}")
            payload = item["payload"]
            t0 = time.time()
            resp = client.post(
                f"{base_url}/api/v1/broll/jobs",
                json=payload,
                headers={**headers, "Idempotency-Key": f"batch-{name}"},
            )
            if resp.status_code != 202:
                record = {"name": name, "status": "rejected", "http_status": resp.status_code, "body": resp.json()}
                summary.append(record)
                print(f"[{name}] Rejected ({resp.status_code}): {resp.json()}")
                continue

            job_id = resp.json()["job_id"]
            while True:
                st = client.get(f"{base_url}/api/v1/jobs/{job_id}", headers=headers).json()
                if st["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.5)

            elapsed = round(time.time() - t0, 2)
            case_dir = os.path.join(out_dir, name)
            os.makedirs(case_dir, exist_ok=True)
            with open(os.path.join(case_dir, "job_result.json"), "w", encoding="utf-8") as out_f:
                json.dump(st, out_f, indent=2)

            summary.append({"name": name, "job_id": job_id, "status": st["status"], "elapsed_s": elapsed, "results": st.get("results")})
            print(f"[{name}] {st['status']} in {elapsed}s -> {st.get('results')}")

    with open(os.path.join(out_dir, "batch_summary.json"), "w", encoding="utf-8") as sf:
        json.dump(summary, sf, indent=2)
    print(f"\nBatch complete. Summary saved to {os.path.join(out_dir, 'batch_summary.json')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IncuBrix B-Roll Batch Runner")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--inputs", default="test_inputs.json")
    parser.add_argument("--out", default="./batch_results")
    parser.add_argument("--token", default="incubrix-demo-token")
    args = parser.parse_args()
    run_batch(args.url, args.inputs, args.out, args.token)