#!/bin/bash
# scripts/init_runpod_pod.sh
# Script to initialize and recover RunPod vLLM pod with required configurations
# This script ensures proper setup of the vLLM container with Qwen3-Coder
# and required environment variables

set -e  # Exit on any error

echo "Initializing RunPod pod for Qwen3-Coder..."

# Ensure we're in the correct directory
cd /workspace || exit 1

# Set required environment variables for vLLM
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_QWEN3_CODER_API_KEY="${VLLM_QWEN3_CODER_API_KEY:-}"
export VLLM_QWEN3_CODER_BASE_URL="${VLLM_QWEN3_CODER_BASE_URL:-}"

# Pull latest vLLM container image
echo "Pulling vLLM container image..."
docker pull vllm/vllm:latest

# Verify container image exists
if ! docker images | grep -q "vllm/vllm"; then
    echo "Error: vLLM container image not found"
    exit 1
fi

# Initialize vLLM server with required flags
echo "Starting vLLM server with optimized settings..."
docker run -d \
  --name qwen3-coder-server \
  --gpus all \
  -p 20128:20128 \
  -e VLLM_USE_FLASHINFER_SAMPLER=0 \
  -e VLLM_QWEN3_CODER_API_KEY="$VLLM_QWEN3_CODER_API_KEY" \
  -e VLLM_QWEN3_CODER_BASE_URL="$VLLM_QWEN3_CODER_BASE_URL" \
  vllm/vllm:latest \
  serve \
  --host 0.0.0.0 \
  --port 20128 \
  --tensor-parallel-size 2 \
  --max-model-len 8192 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder

# Wait for server to be ready
echo "Waiting for vLLM server to become ready..."
for i in {1..30}; do
    if curl -f -s http://localhost:20128/v1/models >/dev/null 2>&1; then
        echo "vLLM server is ready!"
        break
    fi
    echo "Waiting for server... (${i}/30)"
    sleep 2
done

# Perform health check
echo "Performing health check..."
curl -f http://localhost:20128/v1/models || {
    echo "Health check failed!"
    exit 1
}

echo "RunPod pod initialized successfully"