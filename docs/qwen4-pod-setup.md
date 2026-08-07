# Qwen4 Pod Setup — Known Good Configuration

Last verified: 2026-08-05

## Pod Specs

| Parameter | Value |
|-----------|-------|
| Pod ID | `ldtqgcshb2dwsw` |
| GPU | RTX PRO 6000 (96 GB VRAM) |
| Image | `vllm/vllm-openai:latest` |
| Container Disk | 50 GB |
| Cost | $2.09/hr |

## vLLM Docker Args

```
--model Qwen/Qwen3-32B-FP8
--reasoning-parser deepseek_r1
--max-model-len 32768
--host 0.0.0.0
--port 8000
--enable-auto-tool-choice
--tool-call-parser hermes
```

**CRITICAL:** Must include `--enable-auto-tool-choice --tool-call-parser hermes` or tool-calling will 400.

## Environment Variables (in `.env`)

```bash
VLLM_QWEN3_CODER_API_KEY=<runpod-api-key>
VLLM_QWEN3_CODER_BASE_URL=https://<pod-id>-8000.proxy.runpod.net/v1
VLLM_QWEN3_CODER_MODEL=Qwen/Qwen3-32B-FP8
RUNPOD_API_KEY=<runpod-account-key>
```

## Wiring Checklist

### 1. OpenCode CLI config (`~/.config/opencode/opencode.jsonc`)
```json
"qwen3-runpod": {
  "npm": "@ai-sdk/openai-compatible",
  "name": "Qwen4 (RunPod)",
  "options": {
    "baseURL": "https://<pod-id>-8000.proxy.runpod.net/v1",
    "apiKey": "{env:VLLM_QWEN3_CODER_API_KEY}"
  },
  "models": {
    "Qwen/Qwen3-32B-FP8": {
      "name": "Qwen/Qwen3-32B-FP8",
      "tool_call": true,
      "limit": { "context": 32768, "output": 16000 }
    }
  }
}
```

### 2. Provider config (`config/providers.yaml`)
```yaml
max_concurrent_builds: 8  # vLLM handles 8 concurrent with 0.9-1.0s latency

providers:
  qwen3_coder:
    enabled: true
    type: vllm
    name: Qwen4
    gpu_acceleration: true
    base_url: https://<pod-id>-8000.proxy.runpod.net/v1
    model: Qwen/Qwen3-32B-FP8
    role:
      - coding
      - text_task
```

### 3. Router (`core/ai/ai_router.py`)
Qwen4 must be first in every role's provider list:
- `qwen4_coding` for `coding` (coding_agent)
- `qwen4_text` for all text_task roles (planning, architecture, review, etc.)

### 4. Coding implementation (`core/ai_provider.py`)
**DO NOT use opencode CLI for coding** — it panics under concurrent load:
```
thread panicked at fff-core/src/scan.rs
Resource temporarily unavailable
```
Use `_qwen4_coding_run_coding_task` which calls the vLLM chat endpoint directly via `call_qwen4_text()`, writes output to `generated.py`, and commits.

### 5. OmniRoute registration
```bash
# Create provider node (one-time)
curl -b cookies.txt -X POST http://localhost:20128/api/provider-nodes \
  -d '{"name":"qwen4","type":"openai-compatible","baseUrl":"https://<pod-id>-8000.proxy.runpod.net/v1","apiKey":"<key>","isActive":true,"prefix":"runpod","apiType":"chat","chatPath":"/chat/completions","modelsPath":"/models"}'

# Create provider connection using node ID, then set maxConcurrent: 8 via PUT
```

## Concurrency Limits

| Workload | Max Concurrent | Reason |
|----------|---------------|--------|
| Text tasks (vLLM) | 8+ | API handles it, 0.9-1.0s latency |
| Coding agent (opencode CLI) | 1-2 | CLI crashes under load |
| Coding (direct API) | 8 | Same as text tasks |
| Builds in pipeline | 8 | Matches vLLM capacity |

## Known Failure Modes

1. **opencode CLI panics** — if coding uses opencode CLI, >2 concurrent coding tasks crash with `fff-core/src/scan.rs` thread panics → circuit breaker opens → all coding dead
2. **Missing tool-call flags** — without `--enable-auto-tool-choice` on vLLM, opencode returns 400
3. **Deploy on <80GB GPU** — Qwen3-32B-FP8 needs 80GB+ VRAM (RTX 6000 Ada 48GB fails to load)
4. **OmniRoute container crash** — after password reset, container may restart-loop; recreate with `docker compose up -d`
5. **WAITING_FOR_USER_INPUT pile-up** — review script only handled question loops, not first-time questions; fixed to notify Telegram at 1h and auto-answer at 12h

## Verification

```bash
# Test text endpoint
curl -s -H "Authorization: Bearer $KEY" https://<pod-id>-8000.proxy.runpod.net/v1/models

# Test coding (direct API)
.venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv('.env')
from core.ai_provider import _qwen4_coding_run_coding_task
result = _qwen4_coding_run_coding_task('/tmp/test', 'Write hello.py')
print(result['success'])
"

# Test 8-way concurrency
# Should all complete in ~1s each
```
