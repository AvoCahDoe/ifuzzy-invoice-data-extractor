from datasets import load_dataset
import requests
import time
import json
import io

API_URL = "http://localhost:8001"

def evaluate_dataset(max_samples=20):
    print("Loading dataset katanaml-org/invoices-donut-data-v1...")
    try:
        ds = load_dataset('katanaml-org/invoices-donut-data-v1', split='test')
    except ValueError:
        ds = load_dataset('katanaml-org/invoices-donut-data-v1', split='train')
    
    samples = ds.select(range(min(max_samples, len(ds))))
    
    results = []
    
    for idx, item in enumerate(samples):
        print(f"Processing sample {idx+1}/{len(samples)}...")
        image = item['image']
        ground_truth_str = item['ground_truth']
        
        try:
            ground_truth = json.loads(ground_truth_str)
            gt_data = ground_truth.get('gt_parse', {})
        except json.JSONDecodeError:
            print(f"  Failed: Could not parse ground truth JSON.")
            continue
            
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='PNG')
        img_bytes = img_byte_arr.getvalue()
        
        filename = f"test_invoice_{idx}.png"
        files = {"file": (filename, img_bytes, "image/png")}
        
        try:
            resp = requests.post(f"{API_URL}/upload", files=files)
            if resp.status_code != 200:
                print(f"  Failed: Upload returning {resp.status_code}")
                continue
            file_id = resp.json().get('file_id')
        except requests.exceptions.ConnectionError:
            print(f"  Failed: Could not connect to backend at {API_URL}. Is docker-compose running?")
            break
            
        task_payload = {"file_id": file_id, "engine": "rapidocr", "do_structure": True}
        resp = requests.post(f"{API_URL}/task/send", json=task_payload)
        if resp.status_code != 200:
            print(f"  Failed: Task sending returned {resp.status_code}")
            continue
            
        task_id = resp.json().get('task_id')
        
        completed = False
        for _ in range(60):
            resp = requests.get(f"{API_URL}/task/state/{task_id}")
            if resp.status_code == 200:
                state = resp.json()
                if state.get('status') in ['completed', 'validated']:
                    completed = True
                    break
                elif state.get('status') == 'error':
                    print(f"  Task error: {state.get('error')}")
                    break
            time.sleep(1)
            
        if not completed:
            print(f"  Failed: Task timeout.")
            continue
            
        resp = requests.get(f"{API_URL}/task/data/{task_id}")
        if resp.status_code == 200:
            data = resp.json().get('data', {})
            print(f"  Success: Generated structured JSON.")
            results.append({
                "idx": idx,
                "ground_truth": gt_data,
                "extracted": data
            })
        else:
            print(f"  Failed: Could not fetch final data.")
            
    print(f"\nCompleted evaluation for {len(results)} samples.")
    
    report_path = "evaluation_results.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Detailed evaluation results saved to {report_path}")

    # Very naive metric checking logic because actual schema of gt_data is not fully known.
    # We will log the keys present in both.
    if results:
        print("\nSample 0 detailed comparison snippet:")
        print("--- Ground Truth:")
        print(json.dumps(results[0]["ground_truth"], indent=2))
        print("--- Extracted Schema:")
        print(json.dumps(results[0]["extracted"], indent=2))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=5, help="Number of samples to evaluate")
    args = parser.parse_args()
    evaluate_dataset(args.samples)
