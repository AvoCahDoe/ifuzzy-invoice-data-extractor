# ollama.Dockerfile
FROM debian:bullseye-slim

# Install curl and ollama
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://ollama.com/install.sh | sh

# Expose Ollama port
EXPOSE 11434

# Default command: run server and pull model
CMD bash -c "ollama serve & sleep 5 && ollama pull mistral && wait"
