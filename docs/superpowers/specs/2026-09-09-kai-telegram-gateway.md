# KAI Telegram Gateway - Standalone Agent System

**Date:** 2026-09-09  
**Status:** Design approved, ready for implementation  
**Priority:** HIGH - Makes KAI fully independent from Claude Code

---

## Executive Summary

Transform @KaiEnzo_bot into a **complete standalone agent system** accessible via Telegram, making KAI fully independent from Claude Code. When Claude Code runs out of credit, KAI continues operating through Telegram using its own cognitive models (kai-brain, kai-coder, kai-fast on VM 104).

**Core Principle:** Telegram Gateway is a thin interface layer. All intelligence, execution, and state management happens in KAI's existing infrastructure.

---

## Goals

### Primary Goals
1. **Full Claude Code independence** - KAI operates without Claude when credit runs out
2. **Complete agent capabilities** - Chat, file exchange, terminal commands, app building
3. **Persistent sessions** - Conversation context maintained across messages
4. **Secure access** - Single Telegram ID whitelist (612786480)
5. **Smart file handling** - Intelligent file save location detection
6. **Sandboxed execution** - Approval gates for risky operations

### Non-Goals
- Replacing Claude Code (it remains available when credit exists)
- Multi-user access (single user only for now)
- Real-time collaboration features
- Voice/video calls (text + voice messages only)

---

## Architecture

### High-Level Overview

```
┌──────────────────────────────────────────────────────────────┐
│  TELEGRAM (User: 612786480)                                   │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  TELEGRAM GATEWAY SERVICE (new)                               │
│  Location: CT 111 /opt/ai-orchestrator/core/telegram_agent/  │
├──────────────────────────────────────────────────────────────┤
│  • Message Router (parse commands, route to handlers)         │
│  • Session Manager (persistent context per user)              │
│  • File Handler (smart upload/download)                       │
│  • Response Formatter (4096 char limit, auto-file mode)       │
│  • Security Gate (Telegram ID whitelist)                      │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  KAI CORE (existing infrastructure - reused)                  │
├──────────────────────────────────────────────────────────────┤
│  • Cognitive Router → kai-brain/coder/fast (VM 104)           │
│  • Tool Executor (sandboxed: Read, Write, Edit, Bash, etc.)   │
│  • Approval System (existing approval_queue.json)             │
│  • Build Manager (app creation pipeline)                      │
│  • Memory Layer (state persistence)                           │
│  • AI Router (provider fallback chains)                       │
│  • AgentGuard (security monitoring)                           │
└──────────────────────────────────────────────────────────────┘
```

### Component Breakdown

#### 1. Message Router

**Purpose:** Parse incoming Telegram messages and route to appropriate handlers.

**Message Types:**
- **Commands** - `/help`, `/reset`, `/status`, `/files`, `/session`
- **Tool requests** - Natural language (e.g., "read /etc/hosts", "run npm test")
- **Files** - Documents, photos, videos uploaded by user
- **Approvals** - "yes", "no", "approve", "reject"
- **Chat** - General questions/conversation with KAI

**Routing Logic:**
```python
if message.startswith('/'):
    → Command Handler
elif message contains approval intent:
    → Approval Handler (existing)
elif file_attachment:
    → File Handler (new)
else:
    → KAI Chat Handler (existing, enhanced)
```

#### 2. Session Manager

**Purpose:** Maintain persistent conversation context per user.

**Storage Schema:**
```json
{
  "session_id": "tg_612786480_20260909",
  "user_id": "612786480",
  "created_at": "2026-09-09T18:30:00Z",
  "last_activity": "2026-09-09T19:45:00Z",
  "context": {
    "messages": [
      {"role": "user", "content": "read the ai-orchestrator roadmap"},
      {"role": "assistant", "content": "...", "files": ["roadmap_summary.md"]},
      {"role": "user", "content": "what's next?"}
    ],
    "working_directory": "/opt/ai-orchestrator",
    "environment": {"PROJECT_PATH": "/project/ai-orchestrator"},
    "last_files": ["roadmap.json"],
    "pending_approval": null
  }
}
```

**Features:**
- Rolling 40-message window (like kai_chat_history.json)
- Tracks current working directory
- Remembers recently accessed files
- Auto-expire after 24h of inactivity
- `/reset` command clears context, starts fresh

**File:** `memory/telegram_sessions.json`

#### 3. File Handler (Smart Detection)

**Purpose:** Intelligently save uploaded files to appropriate locations.

**Detection Logic:**

| File Type | Destination | Example |
|-----------|-------------|---------|
| `.py`, `.js`, `.ts` | Project dir if identifiable, else `/uploads/` | `app.py` → `/project/myapp/app.py` |
| `.md`, `.txt` (spec-like) | `/docs/superpowers/specs/` | `feature-spec.md` → `/docs/superpowers/specs/2026-09-09-feature-spec.md` |
| `.json`, `.yaml` (config) | `/config/` or project root | `package.json` → current working dir |
| `.env` | **Ask before saving** (security) | `.env` → requires explicit path |
| Images, videos | `/uploads/media/` | `screenshot.png` → `/uploads/media/screenshot.png` |
| Everything else | `/uploads/` | `data.csv` → `/uploads/data.csv` |

**Response Format:**
```
✅ File received: app.py
📁 Saved to: /project/myapp/app.py
📊 Size: 2.4 KB

KAI analyzed the file:
• Python Flask application
• Contains 3 endpoints
• Missing error handling

What would you like me to do with it?
```

#### 4. Response Formatter

**Purpose:** Handle Telegram's 4096 character limit intelligently.

**Strategy:**
- Short response (< 3800 chars) → Send as text
- Long code (> 3800 chars) → `code_response.py` file + summary
- Long logs → `logs.txt` file + summary
- Long explanation → `response.md` file + summary

**Auto-file Mode:**
```python
if len(content) > 3800:
    # Detect content type
    if is_code(content):
        filename = f"response_{timestamp}.py"
    elif is_logs(content):
        filename = f"logs_{timestamp}.txt"
    else:
        filename = f"response_{timestamp}.md"
    
    # Send summary + file
    summary = f"📄 Response too long for Telegram\n\n{content[:500]}...\n\n[Full response in attached file]"
    send_file(filename, content)
    send_message(summary)
```

---

## Tool Execution & Sandboxing

### Permission Levels

| Tool | Permission | Approval Required? |
|------|-----------|-------------------|
| **Read** | ✅ Always allowed | No |
| **Bash (read-only)** | ✅ Commands like `ls`, `cat`, `grep`, `git status` | No |
| **Bash (write)** | ⚠️ Sandboxed | **Yes** - `rm`, `mv`, `npm install`, `systemctl`, etc. |
| **Write** | ⚠️ Restricted paths | **Yes** - outside `/uploads/` or `/tmp/` |
| **Edit** | ⚠️ Restricted paths | **Yes** - system files, configs |
| **Agent** | ⚠️ Spawns subagents | **Yes** - resource intensive |
| **Workflow** | ⚠️ Multi-agent orchestration | **Yes** - high token cost |

### Auto-Allowed Paths
```python
SAFE_PATHS = [
    "/uploads/",
    "/tmp/",
    "/opt/ai-orchestrator/docs/",
    "/project/*/docs/",
]
```

### Restricted Paths (require approval)
```python
RESTRICTED_PATHS = [
    "/etc/",
    "/opt/ai-orchestrator/core/",
    "/opt/ai-orchestrator/memory/",
    "*.env",
    "/root/",
    "/opt/ai-orchestrator/.venv/",
]
```

### Approval Flow Example

```
User: "delete all .pyc files in the project"

KAI: 🚨 APPROVAL REQUIRED

Command: find /project/ai-orchestrator -name "*.pyc" -delete

Risk Level: MEDIUM
- Will delete 247 files
- Cannot be undone
- Affects: /project/ai-orchestrator

Reply "approve" to proceed or "cancel" to abort.
You have 5 minutes to respond.
```

User responds: "approve"

```
KAI: ✅ Approved by user

Executing: find /project/ai-orchestrator -name "*.pyc" -delete

✅ Complete
• 247 files deleted
• 3.2 MB freed
```

**Timeout Behavior:**
- No response in 5 minutes → Auto-cancel
- Send timeout notification: "⏱️ Approval expired for: [command]"

### Command Classifier

```python
# Forbidden (never allow, even with approval)
FORBIDDEN = [
    r'rm -rf /',
    r'dd if=',
    r'mkfs\.',
    r':(){:|:&};:',  # fork bomb
    r'chmod 777',
]

# Needs approval
NEEDS_APPROVAL = [
    r'rm ',
    r'mv ',
    r'systemctl',
    r'npm install',
    r'pip install',
    r'git push',
    r'docker',
]

# Safe (auto-execute)
SAFE = [
    r'ls ',
    r'cat ',
    r'grep ',
    r'find ',  # (read-only flags)
    r'git status',
    r'git log',
    r'git diff',
]
```

### Execution Environment

**Sandbox Limits:**
- Max execution time: 5 minutes per command
- Max output size: 10 MB
- Network: Allowed (needed for git, npm, etc.)
- File system: Limited to KAI's containers (CT 111)

---

## Security Model

### Authentication & Authorization

**Telegram ID Whitelist:**
```python
ALLOWED_USER_IDS = [
    "612786480",  # Primary user
]

def is_authorized(user_id: str) -> bool:
    return user_id in ALLOWED_USER_IDS

def unauthorized_response(user_id: str, username: str):
    log_security_event(
        event_type="unauthorized_access",
        user_id=user_id,
        username=username,
        timestamp=datetime.utcnow()
    )
    
    return (
        "⛔ Unauthorized Access\n\n"
        "This bot is private. Your attempt has been logged.\n"
        f"Your Telegram ID: {user_id}"
    )
```

**Every message checked:**
```python
def handle_message(update):
    user = update.message.from_user
    user_id = str(user.id)
    
    if not is_authorized(user_id):
        send_message(user_id, unauthorized_response(user_id, user.username))
        return
    
    process_authorized_message(update)
```

### Session Security

**Session tokens:** 32-byte cryptographically secure random tokens  
**Expiration:** 24 hours of inactivity  
**Auto-renewal:** On each message  
**Storage:** Encrypted at rest in `memory/telegram_sessions.json`

### Data Protection

**Sensitive data patterns (auto-redacted):**
- API keys: `sk-[a-zA-Z0-9]{48}`
- JWT tokens: `eyJ[a-zA-Z0-9_-]{20,}`
- GitHub tokens: `ghp_[a-zA-Z0-9]{36}`
- Credit cards: `[0-9]{16}`
- Passwords: `password\s*[=:]\s*[\'"][^\'"]+[\'"]`

**File protection:**
- Cannot send: `.env`, `.pem`, `.key`, `.p12` files
- Content scan before sending any file
- Auto-redact sensitive patterns

**Audit logging:**
```json
{
    "timestamp": "2026-09-09T19:45:00Z",
    "user_id": "612786480",
    "session_id": "tg_612786480_1725912300",
    "tool": "Bash",
    "command": "ls /opt/ai-orchestrator",
    "approval_required": false,
    "execution_time": "0.15s",
    "success": true
}
```

**File:** `memory/telegram_agent_audit.json`

### Rate Limiting

```python
RATE_LIMITS = {
    "messages_per_minute": 20,
    "commands_per_hour": 100,
    "file_uploads_per_hour": 50,
    "max_file_size": 20 * 1024 * 1024,  # 20 MB
    "agent_spawns_per_day": 10,
    "workflows_per_day": 5,
}
```

### Command Injection Prevention

**Never execute user input directly:**
```python
# ✅ CORRECT - Parameterized execution
def safe_execute(user_input):
    parsed = parse_natural_language_command(user_input)
    
    result = subprocess.run(
        parsed.args,  # List, not string
        capture_output=True,
        timeout=300,
        cwd=session.working_directory,
        env=sanitize_environment(session.environment)
    )
```

**Input validation:**
- File paths: Must be absolute, no `..` traversal
- Commands: Whitelist-based parsing
- Files: MIME type validation, size limits
- JSON/YAML: Schema validation

---

## Integration Points

### 1. Cognitive Router Integration

**Routes messages to appropriate KAI brain:**
```python
from core.ai.cognitive_router import route_to_cognitive_model

# Coding tasks → kai-coder:7b
# Complex reasoning → kai-brain:latest
# Quick queries → kai-fast:latest

response = route_to_cognitive_model(
    role=determine_role(message),
    prompt=message,
    context=build_context(session),
    max_tokens=4000,
)
```

**Reuses:**
- `core/ai/cognitive_router.py`
- `core/ai/ai_router.py`
- `core/llm_clients.py`
- `memory/provider_quota.json`

### 2. Build Manager Integration

**App creation via Telegram:**
```python
from core.build_manager import create_build

build = create_build(
    name="todo-api",
    description="Flask API for managing todo items",
    operator=f"telegram:{user_id}",
    metadata={"source": "telegram"}
)
```

**Example flow:**
```
User: "Build me a Flask API for managing todo items"

KAI: 🏗️ Build Created: todo-api
Status: PLANNING

[2 minutes later]

KAI: 📋 Architecture Proposal Ready
• Flask 3.0
• SQLite database
• RESTful API (CRUD)
• JWT authentication

Reply "approve" to proceed.
```

**Reuses:**
- `core/build_manager.py`
- `memory/builds.json`
- Existing approval flow

### 3. Memory Layer Integration

**Reuses existing memory system:**
```python
from core.memory import load, save, update

save("telegram_sessions.json", sessions)
sessions = load("telegram_sessions.json")
update("telegram_sessions.json", lambda data: {...})
```

**New memory files:**
- `memory/telegram_sessions.json`
- `memory/telegram_agent_audit.json`
- `memory/telegram_approvals.json`

### 4. AgentGuard Integration

**Security monitoring for all tool executions:**
```python
from core.agentguard.guard import AgentGuard, ActionRequest

guard = AgentGuard(config={"enabled": True})

def execute_tool_with_guard(tool_name, params, session):
    request = ActionRequest(
        agent_id="telegram_agent",
        user_id=session.user_id,
        action_type=map_tool_to_action_type(tool_name),
        resource=params.get("file_path") or params.get("command", ""),
        details=str(params)
    )
    
    result = guard.check_action(request)
    
    if result.decision == Decision.REQUIRE_APPROVAL:
        return queue_approval(request, result)
    
    return execute_tool(tool_name, params, session)
```

**AgentGuard monitors:**
- Read → SAFE (auto-allow)
- Write to /uploads → LOW (auto-allow)
- Write to system → HIGH (approval)
- Bash with `rm` → HIGH (approval)
- Bash with `sudo` → CRITICAL (approval)

---

## Error Handling & Resilience

### Telegram API Failures

**Retry with exponential backoff:**
```python
def send_with_retry(chat_id, message, max_retries=3):
    for attempt in range(max_retries):
        try:
            return telegram_api.send_message(chat_id, message)
        except TelegramAPIError as e:
            if e.error_code == 429:  # Rate limit
                wait = min(2 ** attempt, 60)
                time.sleep(wait)
            elif e.error_code in [400, 403]:
                raise  # Don't retry bad requests
```

**Fallback:** If Telegram down, log to `memory/telegram_agent_errors.json`, KAI scheduler detects and can alert via other channels.

### Session Recovery

**On service restart:**
```python
def restore_sessions():
    sessions = load("telegram_sessions.json")
    
    for session_id, session_data in sessions.items():
        if not session_expired(session_data):
            active_sessions[session_id] = Session.from_dict(session_data)
            
            send_message(
                session_data["user_id"],
                "🔄 KAI Agent Restarted\n\nYour session has been restored."
            )
```

### Cognitive Model Failures

**Fallback chain:**
```python
# kai-brain fails → kai-coder
# kai-coder fails → kai-fast
# All fail → queue for retry, notify user
```

### Approval Timeout Handling

**Auto-cancel with notification:**
```python
async def monitor_approval_timeouts():
    while True:
        await asyncio.sleep(30)
        
        for approval in get_pending_approvals():
            if approval["expires_at"] < now():
                approval["status"] = "expired"
                
                send_message(
                    approval["user_id"],
                    f"⏱️ Approval Expired\n\nCommand: {approval['command']}\nStatus: Cancelled"
                )
```

---

## Deployment

### Service Configuration

**systemd service:**
```ini
# /etc/systemd/system/kai-telegram-agent.service
[Unit]
Description=KAI Telegram Agent (Standalone)
After=network-online.target kai-scheduler.service
Requires=kai-scheduler.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/ai-orchestrator
EnvironmentFile=/etc/ai-orchestrator.env
ExecStart=/opt/ai-orchestrator/.venv/bin/python -m core.telegram_agent
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Location:** CT 111 on Proxmox B (where KAI already lives)

**Dependencies:**
- KAI scheduler (existing)
- Ollama on VM 104 (existing)
- Telegram API (external)

### Environment Variables

Add to `/etc/ai-orchestrator.env`:
```bash
# Already exists
OLLAMA_BASE_URL=http://192.168.1.241:11434
KAI_TELEGRAM_BOT_TOKEN=REPLACE_ME
KAI_TELEGRAM_CHAT_ID=612786480

# New for agent
TELEGRAM_AGENT_ENABLED=true
TELEGRAM_AGENT_SESSION_TTL=86400  # 24 hours
TELEGRAM_AGENT_APPROVAL_TIMEOUT=300  # 5 minutes
```

---

## Testing

### Unit Tests

```python
# tests/test_telegram_agent.py

def test_session_persistence():
    """Session survives service restart."""

def test_file_smart_detection():
    """Files saved to correct locations."""

def test_approval_timeout():
    """Approvals auto-cancel after 5 min."""

def test_command_sanitization():
    """No command injection possible."""

def test_rate_limiting():
    """User cannot exceed rate limits."""
```

### Integration Tests

```python
def test_end_to_end_chat():
    """User sends message → KAI responds via cognitive model."""

def test_file_upload_workflow():
    """User sends .py file → Smart detection → Saved correctly."""

def test_approval_flow():
    """Risky command → Approval request → User approves → Execute."""

def test_build_creation_via_telegram():
    """User requests app → Build created → Approvals → Deploy."""
```

### Manual Testing Checklist

- [ ] Send text message → Get response from kai-brain
- [ ] Upload Python file → Smart detection works
- [ ] Request risky command → Approval flow works
- [ ] Long response → Auto-sent as file
- [ ] `/reset` command → Session cleared
- [ ] `/status` command → System status displayed
- [ ] Build app request → Full pipeline works
- [ ] Service restart → Session restored
- [ ] Unauthorized user → Rejected with audit log
- [ ] AgentGuard integration → High-risk actions blocked

---

## Monitoring

### Health Endpoint

```
GET /telegram-agent/health

{
    "status": "healthy",
    "sessions_active": 1,
    "uptime": "2h 15m",
    "cognitive_models": {
        "kai-brain": "healthy",
        "kai-coder": "healthy",
        "kai-fast": "healthy"
    },
    "telegram_api": "connected",
    "last_message": "2 minutes ago"
}
```

### Metrics

```python
METRICS = {
    "messages_received": Counter,
    "messages_sent": Counter,
    "tool_executions": Counter,
    "approvals_requested": Counter,
    "approvals_granted": Counter,
    "model_calls": Counter,
    "errors": Counter,
    "session_count": Gauge,
}
```

### Logs

- All messages: `journalctl -u kai-telegram-agent -f`
- Approvals: `memory/telegram_approvals.json`
- Audit trail: `memory/telegram_agent_audit.json`
- AgentGuard: `memory/agentguard_audit.json`

---

## Success Criteria

### Phase 1: Core Infrastructure (MVP)
- [x] Design approved
- [ ] Message router implemented
- [ ] Session manager working
- [ ] File handler with smart detection
- [ ] Response formatter (auto-file mode)
- [ ] Security gate (ID whitelist)
- [ ] Integration with cognitive router
- [ ] Basic commands: `/help`, `/reset`, `/status`

### Phase 2: Tool Execution
- [ ] Command classifier (safe/approval/forbidden)
- [ ] Approval flow for risky commands
- [ ] Tool executor (Read, Write, Edit, Bash)
- [ ] Sandboxing enforcement
- [ ] AgentGuard integration
- [ ] Audit logging

### Phase 3: Advanced Features
- [ ] Build Manager integration (app creation)
- [ ] File upload/download working
- [ ] Long response handling (auto-file)
- [ ] Session persistence across restarts
- [ ] Error handling and retries
- [ ] Rate limiting

### Phase 4: Production Readiness
- [ ] All unit tests passing
- [ ] All integration tests passing
- [ ] Manual testing checklist complete
- [ ] AgentGuard monitoring active
- [ ] Health endpoint operational
- [ ] Metrics collection working
- [ ] Documentation complete
- [ ] Deployed to CT 111
- [ ] User acceptance testing

---

## Implementation Plan

Will be created via `superpowers:writing-plans` skill after this spec is approved.

**Estimated effort:** 2-3 days of development + testing

**Deliverables:**
1. `core/telegram_agent/` module (new)
2. `kai-telegram-agent.service` systemd unit
3. Tests in `tests/test_telegram_agent.py`
4. Updated `memory/` schemas
5. AgentGuard integration
6. Documentation

---

## Future Enhancements (Not in Scope)

- Voice message transcription (STT/TTS)
- Multi-user support with role-based access
- Web UI for session management
- Telegram Mini App interface
- Integration with KAI Command Center dashboard
- Telegram bot marketplace (public bot with user accounts)

---

**End of Spec**
