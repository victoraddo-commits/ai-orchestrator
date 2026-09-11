#!/usr/bin/env python3
"""
register-model.py
Generates model registration metadata for KAI's Model Fabric
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

def generate_model_metadata():
    """Generate metadata for GLM-4.7-Flash Q4_K_M GGUF model"""

    model_path = "/opt/kai-cpu-models/zai-org_GLM-4.7-Flash-Q4_K_M.gguf"

    # Check if model exists
    if not os.path.exists(model_path):
        print(f"Warning: Model file not found at {model_path}")
        model_size = 0
    else:
        model_size = os.path.getsize(model_path)

    metadata = {
        "schema_version": 1,
        "model_id": "glm-4.7-flash-q4_k_m-gguf",
        "name": "GLM-4.7-Flash Q4_K_M GGUF",
        "provider": "koboldcpp_cpu",
        "type": "text_generation",
        "architecture": "GLM-4.7-Flash",
        "parameters": "29.9B",
        "quantization": "Q4_K_M",
        "format": "gguf",
        "file_path": model_path,
        "file_size_bytes": model_size,
        "context_length": 32768,  # GLM-4.7-Flash supports 32k context
        "max_tokens": 4096,
        "capabilities": ["text_generation", "reasoning", "code_understanding"],
        "cost_tier": "free_or_low_cost",  # Local inference has minimal cost
        "health_check_endpoint": "http://localhost:8080/health",
        "api_endpoint": "http://localhost:8080/v1/chat/completions",
        "registration_timestamp": datetime.utcnow().isoformat() + "Z",
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "status": "ready" if os.path.exists(model_path) else "model_missing",
        "notes": "Deployed via Dedicated CPU Model Fabric on Proxmox B (VM 112)"
    }

    return metadata

def main():
    """Main entry point"""
    # Ensure output directory exists
    output_dir = "/var/lib/kai/model-fabric"
    os.makedirs(output_dir, exist_ok=True)

    # Generate metadata
    metadata = generate_model_metadata()

    # Write to file
    output_file = os.path.join(output_dir, "glm-4.7-flash-q4_k_m-gguf.json")
    with open(output_file, 'w') as f:
        json.dump(metadata, f, indent=2)

    print(f"Model metadata written to {output_file}")

    # Also create a latest symlink for easy discovery
    latest_link = os.path.join(output_dir, "latest-cpu-model.json")
    if os.path.exists(latest_link):
        os.remove(latest_link)
    os.symlink(os.path.basename(output_file), latest_link)

    print(f"Latest model link created: {latest_link}")

if __name__ == "__main__":
    main()