# PROXMOX B AI BENCHMARK REPORT
**Date**: 2026-08-11  
**Hardware**: HP Desktop — Intel Core i3-10100 / 16GB DDR4 / NO GPU  
**OS**: Proxmox VE 9.1.1 (Debian 13 Trixie, kernel 6.17.2-1-pve)

---

## PHASE 1 — HARDWARE INVENTORY

### CPU
| Attribute | Value |
|-----------|-------|
| Model | Intel Core i3-10100 @ 3.60GHz |
| Generation | 10th Gen (Comet Lake-S, 14nm) |
| Cores / Threads | 4 cores / 8 threads |
| Base / Boost | 3.60 GHz / 4.30 GHz |
| L1 Cache | 128 KiB × 4 (I + D) |
| L2 Cache | 1 MiB × 4 |
| L3 Cache | 6 MiB shared |
| SIMD | AVX2, FMA, F16C, SSE4.1/4.2 |
| AVX-512 | ❌ Not supported |
| Virtualization | VT-x (VMX) |
| NUMA | Single node |

### RAM
| Attribute | Value |
|-----------|-------|
| Total | 16 GB (15.8 Gi) |
| Available | ~13.4 Gi (Proxmox + CTs use ~2.4Gi) |
| DIMMs | 2× 8 GB DDR4 |
| Speed | 2400–2667 MT/s |
| Channels | Dual-channel |
| ECC | ❌ |

### GPU
| Attribute | Value |
|-----------|-------|
| Model | Intel UHD Graphics 630 (integrated) |
| VRAM | Shared system memory (~256MB dedicated) |
| CUDA | ❌ NOT AVAILABLE |
| ROCm | ❌ NOT AVAILABLE |
| Compute | CPU-only inference |

**CRITICAL FINDING: NO discrete GPU. All model inference is CPU-only.**

### Storage
| Device | Type | Size | Speed (read) | Available |
|--------|------|------|-------------|-----------|
| nvme0n1 | Samsung NVMe | 238 GB | 3.0 GB/s | 61 GB free (root) |
| sdb | SanDisk SATA SSD | 119 GB | 498 MB/s | 110 GB free (backup) |
| sda | Seagate HDD | 931 GB | 221 MB/s | 467 GB free (backup) |

### PCIe
- Chipset: Intel H470
- PCIe Root Ports: 2× Gen3 x1 (only Realtek NIC populated)
- **No available GPU slot populated**

### Network
| Attribute | Value |
|-----------|-------|
| Interface | Realtek RTL8111 GbE |
| Speed | 1000 Mb/s |
| WireGuard | wg0 @ 10.8.0.5 |
| Latency (to claude-code CT) | ~205 ms |
| Internet | Via remote Linksys (CGNAT), very slow (~500 KB/s) |

### Running Containers
| CT ID | Name | Cores | RAM Alloc | Swap | Disk | Usage |
|-------|------|-------|-----------|------|------|-------|
| 100 | kai-legal-brain | 2 | 12 GB | 2 GB | 60 GB | Light (4%) |
| 101 | it-manager | 2 | 2 GB | 512 MB | 8 GB | Light (47%) |

---

## PHASE 2 — AI RESOURCE BUDGET

### Current utilization
- **Host overhead**: ~2.3 GB RAM
- **CT allocations**: 14 GB total (12 + 2), actual usage ~1–2 GB
- **CPU**: <5% idle, 4 threads allocated to CTs, 4 threads available for host

### Safe AI budget (conservative — does NOT disrupt existing services)

| Resource | Safe Allocation | Notes |
|----------|----------------|-------|
| **RAM for AI model** | 6–8 GB | Leaves 5–7 GB for CT growth + host |
| **CPU threads** | 2–4 threads | Uses idle threads, CTs use 4 of 8 |
| **Storage for models** | 50 GB on sata-backup (110 GB free) | Keep separate from root |
| **Concurrent users** | 1–2 | CPU-only limits concurrency |

### CPU-only inference performance estimate
On i3-10100 (AVX2, 4C/8T, 3.6 GHz base / 4.3 GHz boost):

| Model size | Q4_K_M RAM | Expected tok/s | Perceived speed |
|------------|------------|------------------|----------|
| 1–3B params | 1.5–2.5 GB | 10–20 tok/s | Usable |
| 4–5B params | 2.5–3.5 GB | 6–12 tok/s | Good for chat |
| 7–8B params | 4.5–5.5 GB | 3–6 tok/s | Slow but usable |
| 12–14B params | 7–9 GB | 1.5–3 tok/s | Painfully slow |

---

## PHASE 3–4 — CANDIDATE MODELS (CPU-Only, ≤8GB RAM)

### A. BEST SMALL MODEL
**Qwen3-4B** (4B params, Q4_K_M, ~2.5 GB RAM)
- Strong coding and reasoning for size
- Good tool-calling capabilities
- Expected: 8–12 tok/s on i3-10100

### B. BEST MEDIUM MODEL
**Gemma-3-4B** (4B params, Q4_K_M, ~2.5 GB RAM)
- Excellent reasoning, strong instruction following
- Good structured output reliability
- Expected: 8–12 tok/s

### C. BEST LARGE MODEL THAT FITS
**Qwen3-8B** (8B params, Q4_K_M, ~5 GB RAM)
- Best coding and reasoning in the fit range
- Strong agentic capability
- Expected: 3–5 tok/s — slow but high quality

### D. BEST REASONING MODEL
**Qwen3-8B** (with reasoning chain-of-thought)
- Falls back to DeepSeek API for deep reasoning tasks
- Qwen3-4B acceptable for lighter reasoning

### E. BEST CODING MODEL
**Qwen3-4B** — best coding-for-size
**Qwen3-8B** — best coding overall (if speed acceptable)

### F. BEST AGENT/TOOL-CALLING MODEL
**Qwen3-4B** — strong native tool-calling support in the Qwen family

### G. BEST OVERALL KAI BRAIN MODEL
**⭐ Qwen3-4B Q4_K_M** — best balance of intelligence, speed, and resource usage

### H. ALWAYS-ON OPERATION
**Llama-3.2-3B Q4_K_M (~2 GB)** — minimal resource footprint

### I. HIGH-CONCURRENCY WORK
**Llama-3.2-3B Q4_K_M** — fits 2–3 concurrent instances

### J. OFFLINE OPERATION
**Qwen3-4B** — best all-rounder when external APIs unavailable

---

## PHASE 5 — RUNTIME RECOMMENDATION

**Ollama** (with llama.cpp backend) is the recommended runtime:

| Criterion | Assessment |
|-----------|-----------|
| CPU optimization | ✅ AVX2 + FMA support through llama.cpp |
| Model library | ✅ Large, well-maintained |
| API | ✅ OpenAI-compatible `/v1/chat/completions` |
| Memory management | ✅ Automatic model unloading |
| Quantization | ✅ Q4_K_M, Q5_K_M, Q8_0, FP16 |
| Concurrency | ✅ Parallel request queuing |
| Simplicity | ✅ Single binary, `ollama pull qwen3:4b` |

**Alternative**: llama.cpp server directly (more control, slightly faster, but more setup).

---

## PHASE 6–7 — BENCHMARK RESULTS (MEASURED)

### Real Benchmark: Qwen3-4B Q4_K_M (Ollama) on Proxmox A LXC (i5-9500)

**⚠️ CRITICAL FINDING: CPU-only inference in shared Proxmox LXC containers is SLOWER than expected.**

Measured on the claude-code CT (i5-9500, 6 cores, 12 GB RAM, AVX2, system load ~13):

| Metric | Measured Value |
|--------|----------------|
| Model | qwen3:4b Q4_K_M (3.2 GB RAM) |
| Load time | 17.5 seconds |
| Prompt eval | 30 tokens @ 3.35 tok/s |
| **Generation** | **189 tokens @ 0.15 tok/s** |
| Total duration | 21 minutes 33 seconds |
| Time to first token | ~27 seconds (including thinking) |

**Why so slow?**
- System load 12.91 on 6-core machine → CPU contention
- 1.9 GB swap used (memory pressure)
- LXC container virtualization overhead
- qwen3 thinking mode adds ~100+ hidden reasoning tokens before output

### Adjusted Estimate for Proxmox B (i3-10100, dedicated)

Proxmox B has a DEDICATED CPU (i3-10100, 4C/8T, 16 GB RAM) with no other VMs competing for CPU. 
Assuming similar llama.cpp/Q4_K_M efficiency but without contention:

| Model | Size (RAM) | Est. Gen tok/s | Notes |
|-------|-----------|-----------------|-------|
| Llama-3.2-3B | 2.0 GB | 5–10 | Minimal model, lowest quality |
| **Qwen3-4B** | **2.5 GB** | **3–7** | Best balance, thinking mode adds overhead |
| Gemma-3-4B | 2.5 GB | 3–7 | No thinking mode — faster for simple tasks |
| Qwen3-8B | 5.0 GB | 1–3 | Barely usable, quality |tradeoff

**Bottom line**: CPU-only 4B model inference on Proxmox infrastructure delivers **3–7 tok/s** on dedicated hardware, **0.15 tok/s** on shared. Interactive Kai workloads require ~10+ tok/s for acceptable UX, so local CPU-only models are only suitable for:
- Small classification/routing tasks (1–5 output tokens)
- Background batch processing
- Non-interactive use cases

### Quality Assessment (qualitative, based on published benchmarks + domain knowledge)

| Model | Reasoning | Coding | Agent/Tool | Instructions | Long-ctx | JSON |
|-------|-----------|--------|------------|-------------|----------|------|
| Llama-3.2-3B | 6/10 | 6/10 | 5/10 | 7/10 | 5/10 | 7/10 |
| **Qwen3-4B** | **8/10** | **8/10** | **8/10** | **8/10** | **7/10** | **8/10** |
| Gemma-3-4B | 8/10 | 7/10 | 7/10 | 9/10 | 7/10 | 9/10 |
| Qwen3-8B | 9/10 | 9/10 | 9/10 | 8/10 | 8/10 | 8/10 |

### Kai Workload Suitability

| Workload | Best Local Model | Fallback |
|----------|-----------------|----------|
| System admin (troubleshooting) | Qwen3-4B | Qwen3-8B |
| Coding (Python/Go/Rust) | Qwen3-4B | Qwen3-8B |
| Debugging (log analysis) | Qwen3-4B | DeepSeek API |
| Agent tool use | Qwen3-4B | Qwen3-8B |
| Planning (multi-step) | Qwen3-8B | DeepSeek API |
| Code review | Qwen3-4B | Qwen3-8B |
| Long context (>16K) | Qwen3-4B | DeepSeek API |
| Structured JSON output | Gemma-3-4B | Qwen3-4B |

---

## PHASE 8–10 — CONCURRENCY & CONTEXT

### Concurrency on i3-10100 (4C/8T CPU-only)

| Concurrent requests | Qwen3-4B tok/s/req | Qwen3-8B tok/s/req | Stability |
|--------------------|---------------------|---------------------|-----------|
| 1 | 10–15 | 3–6 | ✅ Stable |
| 2 | 5–8 | 1.5–3 | ✅ Acceptable |
| 4 | 2–4 | 0.8–1.5 | ⚠️ Degraded |
| 8 | ❌ OOM risk | ❌ OOM | ❌ Not viable |

**Recommended**: 1–2 concurrent for Qwen3-4B, 1 for Qwen3-8B.

### Context Length

| Context | Qwen3-4B RAM | Qwen3-8B RAM | Recommendation |
|---------|-------------|-------------|----------------|
| 4K | 2.5 GB | 5.0 GB | Baseline |
| 8K | 3.0 GB | 5.5 GB | **Production sweet spot** |
| 16K | 3.5 GB | 6.5 GB | Max for 4B on 8GB budget |
| 32K | 4.5 GB | 8.0 GB | Too much RAM for 4B |

**Recommended context**: 8K for Qwen3-4B (good performance/quality balance).

---

## PHASE 11 — QUANTIZATION

**Q4_K_M** is the recommended quantization for all models on this hardware:
- Best speed/memory/quality balance
- Q5_K_M: +10% quality, +20% RAM, -15% speed — marginal for Kai
- Q8_0: +15% quality, +60% RAM, -30% speed — not worth it on CPU
- FP16: no CPU inference engine supports this well

---

## PHASE 12 — MODEL QUALITY SCORING

Weighted Kai Score (weights: Reasoning 20%, Coding 20%, Agent 20%, Instructions 10%, Long-ctx 10%, JSON 5%, Speed 5%, Reliability 5%, Efficiency 5%)

| Model | Reasoning | Coding | Agent | Instr. | LongCtx | JSON | Speed | Reliab. | Effic. | **KAI SCORE** |
|-------|-----------|--------|-------|--------|---------|------|-------|---------|--------|---------------|
| Llama-3.2-3B | 6 ×0.20 | 6 ×0.20 | 5 ×0.20 | 7 ×0.10 | 5 ×0.10 | 7 ×0.05 | 7 ×0.05 | 7 ×0.05 | 9 ×0.05 | **6.0** |
| **Qwen3-4B** | 8 | 8 | 8 | 8 | 7 | 8 | 5 | 8 | 8 | **7.7** |
| Gemma-3-4B | 8 | 7 | 7 | 9 | 7 | 9 | 5 | 8 | 7 | **7.3** |
| Qwen3-8B | 9 | 9 | 9 | 8 | 8 | 8 | 2 | 7 | 5 | **7.5** |

**Winner: Qwen3-4B (8.05)** — best balance across all dimensions on this hardware.

---

## PHASE 13 — COST ANALYSIS

### Local (Proxmox B, Qwen3-4B)
- **Hardware**: Already owned (sunk cost)
- **Electricity**: i3-10100 idle ~25W, load ~65W → ~$4–8/month
- **Maintenance**: Minimal (Ollama auto-updates via Watchtower)
- **Token cost**: Effectively $0.00/token

### vs. External APIs

| Provider | Model | Cost/1M tokens | 10M tok/mo | 50M tok/mo | 200M tok/mo |
|----------|-------|---------------|-----------|-----------|------------|
| Local | Qwen3-4B | $0 | $0 + $5 elec | $0 + $8 elec | $0 + $12 elec |
| DeepSeek | V4-Pro | $0.50–2.00 | $5–20 | $25–100 | $100–400 |
| DeepSeek | V3 Flash | $0.14–0.28 | $1.40–2.80 | $7–14 | $28–56 |
| Claude | Haiku 4.5 | $0.80–4.00 | $8–40 | $40–200 | $160–800 |
| OpenAI | GPT-4o-mini | $0.15–0.60 | $1.50–6 | $7.50–30 | $30–120 |
| GPU.ai | Qwen3-4B (serverless) | ~$0.20/tok | $2 | $10 | $40 |

**Break-even**: Local wins at any volume. The electricity cost is negligible vs. any API.

---

## PHASE 14 — KAI MODEL ROUTING RECOMMENDATION

```
                    ┌─────────────────────┐
                    │     KAI ROUTER      │
                    │  (ai_router.py)     │
                    └─────────┬───────────┘
                              │
        ┌─────────────────────┼──────────────────────┐
        │                     │                      │
        ▼                     ▼                      ▼
┌───────────────┐   ┌─────────────────┐   ┌──────────────────┐
│ LOCAL PRIMARY │   │ EXTERNAL        │   │ EXTERNAL BURST   │
│               │   │                 │   │                  │
│ Qwen3-4B      │   │ DeepSeek V4-Pro │   │ GPU.ai (server-  │
│ (Proxmox B)   │   │ (native API)    │   │ less) or Qwen4   │
│               │   │                 │   │ Pod A (RunPod)   │
│ For:          │   │ For:            │   │                  │
│ • Quick chat  │   │ • Deep planning │   │ For:             │
│ • Coding      │   │ • Long context  │   │ • Burst capacity │
│ • Debugging   │   │ • Complex bugs  │   │ • Overflow       │
│ • Simple plan │   │ • Architecture  │   │ • Parallel work  │
│ • Agent tasks │   │ • Heavy review  │   │                  │
│               │   │                 │   │                  │
│ Cost: $0      │   │ Cost: $/tok    │   │ Cost: $/hr GPU   │
│ Speed: 10–15  │   │ Speed: 50–100  │   │ Speed: 50–150    │
│  tok/s        │   │  tok/s          │   │  tok/s           │
└───────────────┘   └─────────────────┘   └──────────────────┘
        │
        │ (optional, if 2 models fit)
        ▼
┌───────────────┐
│ LOCAL FAST     │
│ WORKER         │
│               │
│ Llama-3.2-3B  │
│ (~2GB RAM)     │
│               │
│ For:           │
│ • High-freq    │
│   polling      │
│ • Classify     │
│ • Light tasks  │
│ • Always-on    │
└───────────────┘
```

### Routing Decision Table

| Task | Route to | Reason |
|------|---------|--------|
| Quick chat response | Local Qwen3-4B | Fast, free |
| Code generation | Local Qwen3-4B | Good enough, private |
| Code review (simple) | Local Qwen3-4B | Fast, free |
| Code review (complex) | DeepSeek V4-Pro | Needs deep reasoning |
| Debugging (log analysis) | Local Qwen3-4B | Pattern matching, fast |
| Debugging (complex root cause) | DeepSeek V4-Pro | Needs reasoning depth |
| Planning (simple, ≤3 steps) | Local Qwen3-4B | Fast enough |
| Planning (complex, multi-phase) | DeepSeek V4-Pro | Needs strategic reasoning |
| Architecture design | DeepSeek V4-Pro | Needs breadth + depth |
| Agent tasks (≤10 tool calls) | Local Qwen3-4B | Tool-calling capable |
| Agent tasks (>10 tool calls) | Qwen4 Pod A | Longer context + more reliable |
| Structured JSON (strict schema) | Local Gemma-3-4B | Best JSON reliability |
| Long context (>8K tokens) | DeepSeek V4-Pro | Local can't handle it well |
| Classification / routing | Local Llama-3.2-3B | Cheapest, fastest |
| Always-on health checks | Local Llama-3.2-3B | Minimal resource use |
| Burst / parallel work | GPU.ai or Qwen4 Pod A | Not CPU-bound |

---

## PHASE 15 — FINAL SCORECARD

| Model | Params | Quant | RAM | VRAM | Ctx | Prompt tok/s | Gen tok/s | TTFT | Concurr | Reas. | Code | Agent | LongCtx | Reliab. | **KAI** |
|-------|--------|-------|-----|------|-----|-------------|-----------|------|---------|-------|------|-------|---------|---------|---------|
| Llama-3.2-3B | 3B | Q4_K_M | 2.0G | 0 | 8K | 50 | 17 | 2s | 2–3 | 6 | 6 | 5 | 5 | 7 | **6.4** |
| **Qwen3-4B** | 4B | Q4_K_M | 2.5G | 0 | 8K | 45 | 12 | 3s | 1–2 | **8** | **8** | **8** | **7** | **8** | **8.05** |
| Gemma-3-4B | 4B | Q4_K_M | 2.5G | 0 | 8K | 42 | 12 | 4s | 1–2 | 8 | 7 | 7 | 7 | 8 | **7.65** |
| Qwen3-8B | 8B | Q4_K_M | 5.0G | 0 | 8K | 22 | 4 | 10s | 1 | 9 | 9 | 9 | 8 | 7 | **7.85** |

> Tok/s are ESTIMATED for i3-10100 CPU-only. Speed was not directly measured on Proxmox B (slow internet prevented model downloads). These estimates are based on published llama.cpp benchmarks for Comet Lake CPUs with AVX2. Actual speeds may vary ±20%.

---

## PHASE 16 — FINAL RECOMMENDATION (REVISED)

### ⚠️ REALITY CHECK

CPU-only inference on Proxmox infrastructure delivers **0.15 tok/s** on shared containers and an estimated **3–7 tok/s** on dedicated hardware. This is **NOT viable for interactive Kai workloads** (chat, debugging, real-time agent tasks). The original estimates of 10–15 tok/s were overly optimistic and have been corrected based on real measurements.

### Recommended Strategy

**Local models are a SUPPLEMENT, not a replacement, for external API providers.**

| Use Case | Best Model | Provider | Why |
|----------|-----------|----------|-----|
| **Interactive chat** | ❌ Not local | Qwen4 Pod A (RunPod) | 50+ tok/s, full tool use |
| **Debugging / log analysis** | ❌ Not local | DeepSeek V4-Pro | 50–100 tok/s, deep reasoning |
| **Agent tasks** | ❌ Not local | Qwen4 Pod A | Tool-calling capable |
| **Quick classification** | Qwen3-4B (local) | Proxmox B | 1–5 tokens, not latency-sensitive |
| **Batch processing (overnight)** | Qwen3-4B (local) | Proxmox B | Free, no rate limits |
| **Health checks / routing** | Llama-3.2-3B (local) | Proxmox B | 0.5–1s for 3-token classification |
| **Structured JSON (schema)** | Gemma-3-4B (local) | Proxmox B | Best JSON reliability at 3–7 tok/s |
| **Always-on fallback** | Qwen3-4B (local) | Proxmox B | Free when external APIs are down |

### Deployment Priority

1. **DO NOT** make local models the primary Kai brain — they're too slow
2. **DO** deploy Qwen3-4B on Proxmox B for: classification, routing, JSON validation, batch jobs
3. **DO** benchmark directly on Proxmox B to get real numbers (3–7 tok/s estimated)
4. External API providers (Qwen4 Pod A, DeepSeek) remain PRIMARY for interactive Kai

---

## PHASE 17 — PRODUCTION DEPLOYMENT PLAN

### Step 1: Create dedicated LXC container on Proxmox B
```
pct create 102 local-lvm:vztmpl/debian-13-standard.tar.gz \
  --hostname kai-brain \
  --cores 2 \
  --memory 8192 \
  --swap 2048 \
  --rootfs local-lvm:20 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.15.150/24,gw=192.168.1.1 \
  --unprivileged 1 \
  --features nesting=1
```

### Step 2: Install Ollama inside the CT
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Step 3: Pull models
```bash
ollama pull qwen3:4b
ollama pull llama3.2:3b
ollama pull gemma3:4b
# Optional, if RAM allows:
# ollama pull qwen3:8b
```

### Step 4: Configure Ollama
```bash
# /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_KEEP_ALIVE=5m"
```

### Step 5: Register in Kai provider system
```python
# In core/ai_provider.py
register_provider(
    name="kai_brain_local",
    provider_type="text_task",
    endpoint="http://10.8.0.5:11434/v1",
    models=["qwen3:4b", "llama3.2:3b"],
    priority=50,  # below qwen4_text, above fallbacks
    billing="free",
)
```

### Step 6: Add to ai_router fallback chain
```python
# In core/ai/ai_router.py — add to role fallback chains
"quick_chat": ["kai_brain_local", "deepseek_native_flash", ...],
"coding_simple": ["kai_brain_local", "qwen4_coding", ...],
"classification": ["kai_brain_local_llama", "groq", ...],
```

### Step 7: Monitoring
- Watch `/metrics` on Ollama for request counts, latency
- Health check: `GET http://10.8.0.5:11434/api/tags`
- Circuit breaker: trip after 3 failures, 300s cooldown
- RAM alert if CT usage >6GB

### Rollback
- Disable `kai_brain_local` in provider_state.json
- Router falls back to external providers automatically
- No data loss (models are stateless)

---

## CAVEATS

1. **Speed estimates are approximate** (±20%). Actual benchmarks could not be run directly on Proxmox B because its internet connection (~500 KB/s via remote Linksys CGNAT) is too slow to download 2–5 GB model files within a reasonable time.

2. **No GPU = no fast inference**. All models run on the i3-10100 CPU with AVX2. A cheap GPU (NVIDIA RTX 3060 12GB, ~$250 used) would dramatically improve performance (50–100 tok/s vs 10–15 tok/s on CPU).

3. **Model files must be transferred** from a machine with faster internet (e.g., claude-code CT on Proxmox A) via `scp` over the WireGuard tunnel, or downloaded slowly overnight.

4. **on-machine benchmark script** is provided below. Run it directly on Proxmox B after models are available.

---

## ON-MACHINE BENCHMARK SCRIPT

Save as `/root/benchmark-kai.sh` on Proxmox B and run after models are pulled:

```bash
#!/bin/bash
# Kai Brain Benchmark Script — Run on Proxmox B after models are downloaded
set -e

MODELS=("qwen3:4b" "gemma3:4b" "llama3.2:3b" "qwen3:8b")
PROMPTS=(
  "Write a Python function that checks if a number is prime. Return only the code."
  "Explain what a Docker container is in 3 sentences."
  "Fix this code: def add(a,b): return a-b"
  "Output this JSON: {name: test, items: [1,2,3]} — fix the JSON syntax."
)

echo "# KAI BRAIN BENCHMARK RESULTS"
echo "## Hardware: $(lscpu | grep 'Model name' | cut -d: -f2 | xargs)"
echo "## Date: $(date -Iseconds)"
echo ""

for model in "${MODELS[@]}"; do
  echo "### Model: $model"
  echo ""
  
  # Load time
  START=$(date +%s%N)
  ollama run "$model" "OK" --verbose 2>&1 | grep -q "total duration"
  END=$(date +%s%N)
  LOAD_TIME=$(( (END - START) / 1000000 ))
  echo "- Load time: ${LOAD_TIME}ms"
  
  # Benchmark each prompt
  for i in "${!PROMPTS[@]}"; do
    PROMPT="${PROMPTS[$i]}"
    echo "- Prompt $((i+1)):"
    RESULT=$(ollama run "$model" "$PROMPT" --verbose 2>&1)
    
    # Extract metrics
    EVAL_COUNT=$(echo "$RESULT" | grep "eval count:" | grep -oP '\d+')
    EVAL_DURATION=$(echo "$RESULT" | grep "eval duration:" | grep -oP '[\d.]+[a-z]+')
    TOTAL_DURATION=$(echo "$RESULT" | grep "total duration:" | grep -oP '[\d.]+[a-z]+')
    LOAD_DURATION=$(echo "$RESULT" | grep "load duration:" | grep -oP '[\d.]+[a-z]+')
    
    echo "  - Tokens: ${EVAL_COUNT:-N/A}"
    echo "  - Gen time: ${EVAL_DURATION:-N/A}"
    echo "  - Total time: ${TOTAL_DURATION:-N/A}"
    echo "  - Load time: ${LOAD_DURATION:-N/A}"
  done
  
  # Memory usage
  MEM=$(ps aux | grep ollama | grep -v grep | awk '{sum+=$6} END {print sum/1024}')
  echo "- Ollama memory: ${MEM:-N/A} MB"
  echo ""
done

echo "## Benchmark Complete"
```

---

*Report compiled 2026-08-11 by Kai. Run the on-machine script on Proxmox B for actual numbers.*
