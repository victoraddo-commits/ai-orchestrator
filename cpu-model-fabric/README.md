# Dedicated CPU Model Fabric

VM 112 (`kai-cpu`) on Proxmox B — CPU-only inference via KoboldCpp serving GLM-4.7-Flash Q4_K_M GGUF.

## Status: DEPLOYED 2026-09-11

| Component | Status |
|-----------|--------|
| VM 112 | ✅ Running (16 cores, 32GB RAM, 100GB EVO) |
| KoboldCpp v1.120 | ✅ Installed (nocuda binary) |
| GLM-4.7-Flash Q4_K_M | ✅ Downloaded (~18GB GGUF) |
| Systemd service | ✅ Enabled |
| KAI provider | ✅ Registered as `koboldcpp_cpu` |

## Access

```bash
# SSH to VM 112
ssh -J root@192.168.99.2,root@100.122.38.118 kai@192.168.1.242

# API endpoint
curl http://192.168.1.242:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello"}],"max_tokens":100}'
```

## Architecture

```
Proxmox B (100.122.38.118)
├── VM 104 (kai-gpu-benchmark) — GPU workloads (Tesla P40)
│   ├── ollama → kai-brain, kai-coder, kai-deep
│   └── llama.cpp servers → local_brain_fast, local_coder
└── VM 112 (kai-cpu) — CPU-only workloads
    └── KoboldCpp → GLM-4.7-Flash Q4_K_M (koboldcpp_cpu provider)
```

## Files

```
cpu-model-fabric/
├── configs/
│   ├── cloud-init.yaml         # Cloud-init for VM 112
│   └── koboldcpp.service       # Systemd service
├── scripts/
│   ├── create-vm.sh            # VM creation script (executed)
│   ├── install-koboldcpp.sh    # Runtime installation
│   └── register-model.py       # Model metadata generator
└── README.md
```

## KAI Integration

Provider `koboldcpp_cpu` registered in `core/ai_provider.py`:
- **Kind**: local
- **Cost tier**: free
- **API**: OpenAI-compatible at `http://192.168.1.242:8080/v1/chat/completions`
- **Health**: SSH → `curl http://localhost:8080/api/v1/info`
- **Role**: Independent CPU capacity — doesn't contend with GPU workloads on VM 104

## Operations

```bash
# Service management
sudo systemctl start koboldcpp
sudo systemctl status koboldcpp
sudo journalctl -u koboldcpp -f

# Health check
curl http://localhost:8080/api/v1/info
curl http://localhost:8080/v1/models
```
