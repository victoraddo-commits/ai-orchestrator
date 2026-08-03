# Juris Kai - Legal Expert Assistant

Juris Kai is a multi-tenant legal expertise assistant built on the same security principles as the law_tutor module. It provides educational legal guidance and explanations without any access to operational capabilities.

## Security Boundaries

Like law_tutor, Juris Kai enforces strict security boundaries:
- Cannot import `core.build_manager`, `core.approval`, or `core.deployment_manager`
- Only uses text_task providers, never coding agents
- Requires authorization via environment variable `JURIS_KAI_CHAT_ID`
- All responses are text-only, no executable code or tool use

## Features

- Legal teaching and concept explanations
- Case analysis and judicial reasoning
- Legal research and documentation
- Argument construction and IRAC methodology
- Flashcard generation for study
- Learning progress tracking

## Usage

Set the `JURIS_KAI_CHAT_ID` environment variable to authorize users:
```bash
export JURIS_KAI_CHAT_ID="your_telegram_chat_id_here"
```

Then configure your Telegram bot to use the juris_kai module.

## Providers Used

The module leverages the following AI providers for legal expertise:
- Qwen3-Coder (RunPod RTX 5090) - Paid per-request GPU billing
- Claude and OpenAI - For reasoning and analysis
- Gemini and DeepSeek - For research and documentation