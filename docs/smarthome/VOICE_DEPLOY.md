# KAI Voice — deployment (2026-09-25)

## Service
`systemd kai-voice-gateway.service` — standalone aiohttp gateway on
**127.0.0.1:8130**, `Restart=always`, **enabled at boot**.
- `KAI_VOICE_BIND=127.0.0.1`, `KAI_VOICE_PORT=8130`, `KAI_VOICE_TLS=off`
  (loopback only; the router probes http, so TLS on the loopback is disabled).
- Route: `python -m core.voice_gateway.gateway`.

## Models
- **TTS:** piper `en_US-lessac-medium.onnx` (63 MB) at
  `/root/.local/share/piper/voices` (`PIPER_MODEL_DIR`).
- **STT:** faster-whisper `small.en` (cached on disk).

## Endpoints (added to the standalone runner)
| Path | Purpose |
|---|---|
| `GET /health` | readiness |
| `GET/POST /speak?text=` | real piper TTS -> WAV |
| `POST /transcribe` | real whisper STT (accepts raw PCM16 or multipart WAV) |

## KAI side
- `/api/voice/status` probes the real gateway -> `{gateway_up, stt, tts, available}`.
- `/kai/voice/speak`, `/kai/voice/transcribe`, `/kai/voice/chat` are the user routes.
- `voice_router` read timeout raised 30s -> 120s (first whisper load).

## Verified
- `/kai/voice/speak` -> **200**, real WAV.
- Round-trip: TTS "the quick brown fox…" -> STT -> **exact match**.
- `/api/voice/status` -> `available: true, stt: true, tts: true`.

## Honest notes
- The gateway is loopback-only; expose on the LAN only behind auth if ever needed.
- If piper's onnxruntime emits `pthread_setaffinity_np failed` warnings, they are
  benign (noisy affinity hint on the Proxmox CPU set).
