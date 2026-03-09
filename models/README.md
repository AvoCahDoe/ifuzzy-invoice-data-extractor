# 🧠 Models & Llama.cpp Engine

This directory contains the scripts and storage for the Large Language Models (LLMs) used in the extraction pipeline. The system is designed to run local GGUF models via the `llama-server` (part of the `llama.cpp` project).

## 🚀 Key Features

- **Local Inference**: All LLM processing is performed locally on the host CPU. No external API keys are required.
- **Automated Downloads**: The `start_llama.sh` script automatically fetches the required model weights from HuggingFace if they are not already present.
- **Precision Levels**: Supports multiple model precisions (Q4_0, Q5_0, Q8_0, F16) to balance speed and accuracy.
- **Concurrent Servers**: The system can orchestrate multiple `llama-server` instances on different ports to handle various model sizes (e.g., 350M vs 1.2B) simultaneously.

## 🛠️ Components

### `start_llama.sh`

The primary entrypoint for the `llamacpp` container. It:

1. Checks for the presence of the `LFM2-1.2B-Extract-Q8_0.gguf` model.
2. Downloads it using `curl` if missing.
3. Launches the `llama-server` with optimized parameters (e.g., 8192 context window).

### `LFM2` Models

The pipeline is optimized for the **LFM2** (Liquid Foundation Model) series, specifically fine-tuned for structured data extraction. These models are tiny but highly capable of parsing complex markdown tables and generating strictly validated JSON.

## 🏗️ Technical Details

- **Model Mount**: The `models/` directory is mounted into the `llamacpp` container at `/models`.
- **Context Window**: Configured to 8192 tokens to comfortably process multi-page invoices.
- **Server Ports**:
  - `8080`: Default port for the primary 1.2B model (Q8_0).
  - `8081-8084`: Used when running comparison tasks across different precisions.

## 🧪 Usage

To manually download the model:

```bash
bash start_llama.sh
```

The script will ensure the `.gguf` file is placed in the current directory, where the Docker container can access it.
