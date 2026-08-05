# Qwen3 direct routing (delayed import to avoid circular issues)
try:
    from .direct_qwen3_override import _qwen3_router
    print("🔧 Qwen3 direct routing module loaded")
except ImportError as e:
    print(f"⚠️ Qwen3 routing module not available: {e}")

