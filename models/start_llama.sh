#!/bin/sh

# Model URLs
URL_Q4="https://huggingface.co/LiquidAI/LFM2-1.2B-Extract-GGUF/resolve/main/LFM2-1.2B-Extract-Q4_0.gguf"
URL_Q5="https://huggingface.co/LiquidAI/LFM2-1.2B-Extract-GGUF/resolve/main/LFM2-1.2B-Extract-Q5_K_M.gguf"
URL_Q8="https://huggingface.co/LiquidAI/LFM2-1.2B-Extract-GGUF/resolve/main/LFM2-1.2B-Extract-Q8_0.gguf"
URL_F16="https://huggingface.co/LiquidAI/LFM2-1.2B-Extract-GGUF/resolve/main/LFM2-1.2B-Extract-F16.gguf"
URL_350M="https://huggingface.co/LiquidAI/LFM2-350M-Extract-GGUF/resolve/main/LFM2-350M-Extract-Q8_0.gguf"

# Model Paths
DIR="/models"
PATH_Q4="$DIR/LFM2-1.2B-Extract-Q4_0.gguf"
PATH_Q5="$DIR/LFM2-1.2B-Extract-Q5_K_M.gguf"
PATH_Q8="$DIR/LFM2-1.2B-Extract-Q8_0.gguf"
PATH_F16="$DIR/LFM2-1.2B-Extract-F16.gguf"
PATH_350M="$DIR/LFM2-350M-Extract-Q8_0.gguf"

# Ensure wget is available
if ! command -v wget >/dev/null 2>&1; then
    apt-get update && apt-get install -y wget
fi

# Download missing models
download_if_missing() {
    if [ ! -f "$1" ]; then
        echo "Downloading $1..."
        wget -q --show-progress -O "$1" "$2"
    else
        echo "Found $1"
    fi
}

download_if_missing "$PATH_Q4" "$URL_Q4"
download_if_missing "$PATH_Q5" "$URL_Q5"
download_if_missing "$PATH_Q8" "$URL_Q8"
download_if_missing "$PATH_F16" "$URL_F16"
download_if_missing "$PATH_350M" "$URL_350M"

echo "Launching parallel llama.cpp servers..."

# Ensure KV cache directory exists
mkdir -p "$DIR/kv_cache"

# Shared parameters
# -t 4: Limit threads per server to follow physical core count logic (4x4 = 16 max threads)
# --mlock: Lock models in RAM to prevent swapping
OPTS="-c 8192 -t 4 --mlock --n-gpu-layers 0 --host 0.0.0.0 --slot-save-path /models/kv_cache"

# Start servers in background
# Port 8080: Q8_0 (Default)
/app/llama-server -m "$PATH_Q8" --port 8080 $OPTS &
# Port 8081: Q4_0
/app/llama-server -m "$PATH_Q4" --port 8081 $OPTS &
# Port 8082: Q5_K_M
/app/llama-server -m "$PATH_Q5" --port 8082 $OPTS &
# Port 8083: F16
/app/llama-server -m "$PATH_F16" --port 8083 $OPTS &
# Port 8084: 350M
/app/llama-server -m "$PATH_350M" --port 8084 $OPTS &

echo "All servers started. Monitoring..."
# Keep container alive and wait for all background processes
wait
