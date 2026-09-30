"""Money SMS bridge — Phase 2c (project spec section 15/70).

Forwards copies of non-OTP inbound SMS from the CT111 SMS worker to the
akush-core internal ingest endpoint on CT108. A financial copy only; the SMS
worker and the registration inbox are untouched. OTP bodies are NEVER
transmitted (defense-in-depth skip mirrors akush-core contract 422).
"""
