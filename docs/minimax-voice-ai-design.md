# 17N: MiniMax Voice/Phone AI — Design Proposal

**Status**: Design proposal (no implementation yet)
**Date**: 2026-08-06
**Phase**: 17N
**Priority**: 60 (lowest — placed last deliberately)

## Executive Summary

MiniMax rates highly for natural conversation, voice generation, character/role-play, multilingual support, and speech understanding. The user's assessment positions it for customer-facing AI / voice assistant / phone agent roles. This document outlines what a MiniMax voice/phone integration would look like for the Kai platform, including telephony provider options, speech pipeline architecture, integration points with existing apps, and realistic use cases.

**No code is written from this document until the user reviews and approves the design.**

---

## 1. Current State

### What exists today
- Every task_type in `core.ai.ai_router.ROLE_PROVIDERS` is text/code only (planning, architecture, coding, review, documentation, log_analysis, classification, legal)
- No inbound/outbound calling integration
- No speech-to-text (STT) or text-to-speech (TTS) pipeline
- No telephony provider account connected to the system
- MiniMax is already registered as a text provider (`minimax`) but excluded from routing (0/4 verified for text tasks)

### What MiniMax brings
MiniMax's strengths (per the user's provider comparison dated 2026-07-31):
- Natural conversation ability
- Voice generation quality
- Character/role-play capability
- Multilingual support
- Speech understanding

---

## 2. Architecture Options

### Option A: MiniMax API Direct (Voice-only, No Telephony)

Use MiniMax's native voice API for speech interaction without phone calling:

```
User (browser/app mic) → STT (MiniMax/Whisper) → Kai reasoning (existing text pipeline)
    → TTS (MiniMax voice generation) → Audio output (browser/app speaker)
```

**Pros**: No telephony provider needed, leverages MiniMax's voice quality directly
**Cons**: No phone calling, browser-only, limited to Kai dashboard/web app

**Cost**: MiniMax API pricing for voice generation (per-character or per-second, TBD)

### Option B: MiniMax + Twilio (Full Phone AI)

Full telephony integration for inbound/outbound calling:

```
Phone call ↔ Twilio (SIP/PSTN) → Media Streams (WebSocket) 
    → STT (Deepgram/Whisper/MiniMax) → Kai reasoning (text pipeline)
    → TTS (MiniMax voice) → Media Streams → Twilio → Phone call
```

**Components**:
1. **Twilio**: Phone number provisioning, call routing, Media Streams for real-time audio
2. **STT Engine**: Deepgram (lowest latency), OpenAI Whisper, or MiniMax's own STT
3. **Kai Text Pipeline**: Existing `ai_router.delegate()` for reasoning/response generation
4. **TTS Engine**: MiniMax voice generation (primary strength)
5. **WebSocket Server**: Bridges Twilio Media Streams ↔ STT/TTS engines (Python `websockets` or FastAPI WebSocket)

**Pros**: Full phone calling, PSTN reach, SMS fallback
**Cons**: Twilio cost ($1-2/month per number + per-minute), complex WebSocket pipeline, latency challenges

**Cost estimate**: ~$5-10/month base (1 phone number + low call volume) plus MiniMax API costs

### Option C: MiniMax + Telegram Voice (Existing Bridge)

Leverage the existing Telegram bridge (13Z) for voice messages:

```
Telegram voice message → Telegram Bot API (voice file) → STT (Whisper local/API)
    → Kai reasoning (text pipeline) → TTS (MiniMax voice) → Voice reply via Telegram
```

**Pros**: Reuses existing Telegram infrastructure, no additional provider, incremental
**Cons**: Telegram-only, async (not real-time conversation), limited to voice messages not live calls

**Cost**: Minimal (MiniMax TTS API only)

---

## 3. Recommended Path: Option C → Option B (Incremental)

### Phase 1: Telegram Voice (Option C) — ~2-3 days
1. Add voice message handling to the Telegram bot (receive voice → transcribe → process → respond)
2. Integrate MiniMax TTS for voice responses
3. Support voice commands for existing Kai capabilities (status checks, build queries, approvals)

### Phase 2: Full Phone AI (Option B) — ~2-3 weeks
1. Provision Twilio phone number
2. Build WebSocket Media Streams server
3. Wire STT → Kai reasoning → MiniMax TTS pipeline
4. Implement call handling (greeting, intent routing, handoff)

---

## 4. Integration Points with Existing Apps

| App | Voice Use Case | Priority |
|-----|---------------|----------|
| **Kai Dashboard** | Voice status queries ("Kai, how many builds are running?") | High |
| **Kai Telegram Bot** | Voice commands via Telegram messages | High |
| **IT Manager** | Customer status checks, ticket creation by voice | Medium |
| **SUSU** | Balance inquiry, contribution status by voice | Low |
| **ProxDash** | Infrastructure status readout | Medium |

---

## 5. Realistic Use Cases

1. **AI Receptionist**: Answer common questions about homelab status, route complex queries to the operator
2. **Status Voice Queries**: "Hey Kai, how many builds are running?" → voice response with counts
3. **Alert Notifications**: Kai calls the operator when critical alerts fire (budget exceeded, build failure, pod down)
4. **Approval by Voice**: "Kai, approve build #42" → voice confirmation
5. **Voice-driven Build Requests**: "Kai, build a new SUSU feature for payout scheduling"

---

## 6. Telephony Provider Comparison

| Provider | Strengths | Weaknesses | Cost |
|----------|-----------|------------|------|
| **Twilio** | Mature API, Media Streams, global numbers | Per-minute costs, complex setup | $$ |
| **Telnyx** | Lower latency, competitive pricing | Smaller ecosystem | $ |
| **Vonage** | Good voice quality, simple API | Less flexible streaming | $$ |
| **Plivo** | Affordable, good docs | Limited real-time streaming | $ |

**Recommendation**: Twilio — best Media Streams support for real-time AI voice, largest community, most examples for AI voice agents.

---

## 7. Technical Decisions to Resolve

Before any implementation begins, the user must decide:

1. **Scope**: Telegram voice only (Option C), or full phone AI (Option B)?
2. **Telephony provider**: Twilio, Telnyx, or other?
3. **STT engine**: Deepgram (lowest latency, paid), Whisper (local/free, higher latency), or MiniMax native?
4. **Phone number**: New dedicated number, or port existing?
5. **Languages**: English only, or multilingual (MiniMax's strength)?
6. **Budget**: Hard monthly ceiling for voice/telephony costs?

---

## 8. Dependencies

- **13Z (Telegram Bridge)**: Required for Option C — already built and operational
- **16D (Cost Budget)**: Required for cost tracking — already built and wired to scheduler
- **15A (Auth)**: Required for authenticated voice commands — already built
- **MiniMax API key**: Must be provisioned and verified before any TTS integration

---

## 9. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| MiniMax API quality degrades | Medium | Fallback to ElevenLabs or local TTS |
| Real-time latency too high for calls | High | Start with async Telegram voice; benchmark before live calls |
| Cost overrun on phone calls | Medium | Hard budget ceiling + 16D alerts |
| Twilio Media Streams complexity | Medium | Start with simple call-and-response before streaming |
| RTX 5090 vLLM instability (if used for STT) | Low | Use dedicated STT service, not self-hosted |

---

**Next step**: User reviews this proposal and selects scope/options before any code is written.

🤖 Generated by Kai/Fable per operator directive
