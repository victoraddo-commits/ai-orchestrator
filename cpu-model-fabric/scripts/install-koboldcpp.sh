#!/bin/bash
# install-koboldcpp.sh
# Installs KoboldCpp for CPU Model Fabric

set -euo pipefail

LOG_FILE="/var/log/kai-cpu/install-koboldcpp.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Starting KoboldCpp Installation ==="
echo "Date: $(date)"
echo "Host: $(hostname)"

# Create directories
mkdir -p /opt/koboldcpp
mkdir -p /opt/kai-cpu-models
mkdir -p /var/log/kai-cpu

# Update system
echo "Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# Install dependencies
echo "Installing dependencies..."
apt-get install -y -qq wget ca-certificates

# Download KoboldCpp (latest release as of 2026-09-11)
KOBOLDCPP_URL="https://github.com/LostRuins/koboldcpp/releases/download/v1.73.0/koboldcpp-linux-x64"
KOBOLDCPP_PATH="/opt/koboldcpp/koboldcpp"

echo "Downloading KoboldCpp from $KOBOLDCPP_URL..."
wget -q --show-progress -O "$KOBOLDCPP_PATH" "$KOBOLDCPP_URL"
chmod +x "$KOBOLDCPP_PATH"

# Verify installation
if [[ -x "$KOBOLDCPP_PATH" ]]; then
    echo "✓ KoboldCpp installed successfully to $KOBOLDCPP_PATH"
    "$KOBOLDCPP_PATH" --version
else
    echo "✗ Failed to install KoboldCpp"
    exit 1
fi

# Download llama.cpp for validation
echo "Setting up llama.cpp for validation..."
LLAMA_CPP_DIR="/opt/llama.cpp"
if [[ ! -d "$LLAMA_CPP_DIR" ]]; then
    git clone https://github.com/ggerganov/llama.cpp.git "$LLAMA_CPP_DIR"
    cd "$LLAMA_CPP_DIR"
    # Checkout a specific stable commit
    git checkout b3386d1
    # Build with AVX2 support for better performance on modern CPUs
    make LLAMA_AVX2=1 clean
    make LLAMA_AVX2=1 -j$(nproc)
    echo "✓ llama.cpp built successfully"
else
    echo "✓ llama.cpp already present"
fi

# Create model directory structure
echo "Setting up model directory..."
chown -R kai:kai /opt/kai-cpu-models /opt/koboldcpp /opt/llama.cpp /var/log/kai-cpu

echo "=== KoboldCpp Installation Complete ==="
echo "Next steps:"
echo "1. Copy GLM-4.7-Flash Q4_K_M GGUF model to /opt/kai-cpu-models/"
echo "2. Configure and start the KoboldCpp service"
echo "3. Test model inference"