# Qwen4 direct routing (delayed import to avoid circular issues)
try:
    from .direct_qwen4_override import _qwen4_router
    import sys as _sys
    print("🔧 Qwen4 direct routing module loaded", file=_sys.stderr)
except ImportError as e:
    import sys as _sys
    print(f"⚠️ Qwen4 routing module not available: {e}", file=_sys.stderr)
