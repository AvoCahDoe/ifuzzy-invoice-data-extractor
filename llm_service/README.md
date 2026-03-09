# 🤖 LLM Service (Legacy Support)

This directory contains configuration files for alternative LLM deployments, specifically for use with [Ollama](https://ollama.com/) or direct model management via Modelfiles.

## 🚀 Key Features

- **Modelfile Configuration**: Includes `Modelfile.invoice`, which defines the system prompt and parameters for fine-tuned extraction models.
- **Custom Model definition**: Allows for building custom Ollama images optimized for invoice parsing tasks.

## 🛠️ Usage

This service is currently used as a reference or for legacy support. The primary pipeline uses the `llamacpp` container for optimized CPU inference.

### Building an Ollama Model

If using Ollama, you can create the optimized model using:

```bash
ollama create invoice-extractor -f Modelfile.invoice
```

## 🏗️ Technical Details

- **`Modelfile.invoice`**: Sets the temperature to `0`, context length, and the strict system prompt required for JSON output.

## 🧪 Development

To use this with a local Ollama instance:

1. Ensure Ollama is installed and running.
2. Run the `ollama create` command above.
3. Update the backend `LLAMA_CPP_HOST` to point to your Ollama API endpoint.
