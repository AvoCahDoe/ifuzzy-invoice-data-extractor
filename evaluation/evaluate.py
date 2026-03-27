from datasets import load_dataset
import requests
import time
import json
import io
import matplotlib.pyplot as plt
from rapidfuzz import fuzz

API_URL = "http://localhost:8001"

def evaluate_dataset(max_samples=200):
    print(f"Loading dataset katanaml-org/invoices-donut-data-v1 (requested {max_samples} samples)...")
    
    # Try test split first, but if max_samples > test size (26), use train split (425)
    split_to_use = 'test'
    if max_samples > 26:
        split_to_use = 'train'
        print(f"  Switching to '{split_to_use}' split to accommodate large sample size.")
    
    try:
        ds = load_dataset('katanaml-org/invoices-donut-data-v1', split=split_to_use)
    except Exception as e:
        print(f"  Error loading split {split_to_use}: {e}. Falling back to default.")
        ds = load_dataset('katanaml-org/invoices-donut-data-v1', split='train')
    
    samples = ds.select(range(min(max_samples, len(ds))))
    
    results = []
    stats = {"success": 0, "failed": 0, "timeout": 0, "error": 0}
    
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
            stats["timeout"] += 1
            continue
            
        resp = requests.get(f"{API_URL}/task/data/{task_id}")
        if resp.status_code == 200:
            resp_json = resp.json()
            data = resp_json.get('data', {})
            
            # Extract metadata from the response if available
            proc_time = resp_json.get('processing_time', 0)
            viz_conf = resp_json.get('avg_visual_confidence', 0)
            
            print(f"  Success: Generated structured JSON in {proc_time:.2f}s.")
            stats["success"] += 1
            results.append({
                "idx": idx,
                "ground_truth": gt_data,
                "extracted": data,
                "processing_time": proc_time,
                "visual_confidence": viz_conf
            })
        else:
            print(f"  Failed: Could not fetch final data.")
            stats["error"] += 1
            
    print(f"\nCompleted evaluation for {len(results)} samples.")
    print(f"Stats: {stats}")
    
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
        
        # Calculate accuracy and generate full report
        calculate_accuracy_and_plot(results, stats)

def calculate_accuracy_and_plot(results, stats):
    metrics = {"Invoice Number": [], "Vendor Name": [], "Customer Name": []}
    proc_times = []
    viz_confs = []
    match_scores = [] # For correlation
    
    for res in results:
        gt = res.get("ground_truth", {})
        ext = res.get("extracted", {})
        
        gt_header = gt.get("header", {})
        
        # 1. Invoice Number
        gt_inv = str(gt_header.get("invoice_no", "")).strip()
        ext_inv = str(ext.get("invoice_number", "") or "").strip()
        score_inv = fuzz.ratio(gt_inv.lower(), ext_inv.lower()) if gt_inv else 0
        if gt_inv:
            metrics["Invoice Number"].append(score_inv)
        
        # 2. Vendor Name
        gt_vendor = str(gt_header.get("seller", "")).strip()
        ext_vendor = str(ext.get("vendor_name", "") or "").strip()
        score_vendor = fuzz.partial_ratio(gt_vendor.lower(), ext_vendor.lower()) if gt_vendor and ext_vendor else 0
        if gt_vendor:
            metrics["Vendor Name"].append(score_vendor)
            
        # 3. Customer Name
        gt_client = str(gt_header.get("client", "")).strip()
        ext_client = str(ext.get("customer_name", "") or "").strip()
        score_client = fuzz.partial_ratio(gt_client.lower(), ext_client.lower()) if gt_client and ext_client else 0
        if gt_client:
            metrics["Customer Name"].append(score_client)

        proc_times.append(res.get("processing_time", 0))
        viz_confs.append(res.get("visual_confidence", 0) * 100) # Convert to 0-100
        
        # Weighted average for overall correlation
        avg_match = (score_inv + score_vendor + score_client) / 3
        match_scores.append(avg_match)

    # --- GRAPH 1: ACCURACY BAR CHART ---
    avg_metrics = {k: sum(v) / len(v) for k, v in metrics.items() if v}
    plt.figure(figsize=(10, 6))
    names = list(avg_metrics.keys())
    values = list(avg_metrics.values())
    bars = plt.bar(names, values, color=['#4CAF50', '#2196F3', '#FFC107'])
    plt.ylim(0, 110)
    plt.ylabel('Average Match Score (%)')
    plt.title('Field Extraction Accuracy (n={})'.format(len(results)))
    for bar in bars:
        yval = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2, yval + 2, f"{yval:.1f}%", ha='center', va='bottom', fontweight='bold')
    plt.tight_layout()
    plt.savefig("accuracy_report.png", dpi=300)
    plt.close()

    # --- GRAPH 2: PERFORMANCE HISTOGRAM ---
    plt.figure(figsize=(10, 6))
    plt.hist(proc_times, bins=20, color='#9C27B0', edgecolor='black', alpha=0.7)
    plt.xlabel('Processing Time (seconds)')
    plt.ylabel('Frequency')
    plt.title('System Performance Distribution')
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    plt.tight_layout()
    plt.savefig("performance_report.png", dpi=300)
    plt.close()

    # --- GRAPH 3: CONFIDENCE VS ACCURACY SCATTER ---
    plt.figure(figsize=(10, 6))
    plt.scatter(viz_confs, match_scores, color='#FF5722', alpha=0.6)
    plt.xlabel('OCR Visual Confidence (%)')
    plt.ylabel('Actual Field Match Score (%)')
    plt.title('OCR Confidence vs. Extraction Accuracy')
    plt.xlim(0, 105)
    plt.ylim(0, 105)
    plt.grid(True, linestyle=':', alpha=0.6)
    plt.tight_layout()
    plt.savefig("confidence_correlation.png", dpi=300)
    plt.close()

    # --- GRAPH 4: STATUS DISTRIBUTION PIE ---
    plt.figure(figsize=(8, 8))
    labels = [k.capitalize() for k in stats.keys() if stats[k] > 0]
    sizes = [stats[k.lower()] for k in labels if stats[k.lower()] > 0]
    colors = ['#4CAF50', '#F44336', '#FF9800', '#9E9E9E']
    plt.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=140, colors=colors[:len(labels)])
    plt.title('Overall Extraction Success Rate')
    plt.axis('equal')
    plt.tight_layout()
    plt.savefig("status_distribution.png", dpi=300)
    plt.close()

    print(f"\n[!] 4 graphs generated and saved to 'evaluation/' folder.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=5, help="Number of samples to evaluate")
    args = parser.parse_args()
    evaluate_dataset(args.samples)
