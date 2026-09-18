-- KAI Media Revenue Factory — reference seed data (migration 003)
-- Only static reference rows. No trend / analytics / revenue data is ever
-- seeded: those must come from real observations.

INSERT INTO platforms (key, name, kind, status) VALUES
    ('youtube', 'YouTube', 'video', 'BLOCKED'),
    ('tiktok',  'TikTok',  'video', 'BLOCKED')
ON CONFLICT (key) DO NOTHING;

INSERT INTO factories (key, name, kind, description, status) VALUES
    ('public_domain_cartoon', 'Public-Domain Cartoon', 'factory_a',
     'Public-domain works with original transformation (Factory A)', 'PARTIALLY_VERIFIED'),
    ('original_ai_drama', 'Original AI Drama', 'factory_b',
     'Original universes, characters and episodic drama (Factory B)', 'PARTIALLY_VERIFIED')
ON CONFLICT (key) DO NOTHING;

INSERT INTO models (key, provider, endpoint, role, status, meta) VALUES
    ('qwen3-coder:kai', 'ollama', 'http://127.0.0.1:11434', 'content',
     'PARTIALLY_VERIFIED', '{"note":"local model; generation verified, content quality unverified"}'::jsonb)
ON CONFLICT (key) DO NOTHING;

INSERT INTO policies (key, name, category, severity, status, rule) VALUES
    ('no_fabricated_analytics', 'Never fabricate analytics', 'integrity', 'critical',
     'VERIFIED', '{"rule":"analytics.value must originate from a real source or manual entry"}'::jsonb),
    ('no_fabricated_revenue', 'Never fabricate revenue', 'integrity', 'critical',
     'VERIFIED', '{"rule":"revenue.amount must originate from a verified source"}'::jsonb),
    ('rights_before_publish', 'Rights/provenance before publishing', 'rights', 'critical',
     'VERIFIED', '{"rule":"publishing is blocked without a rights/provenance record"}'::jsonb),
    ('synthetic_disclosure', 'Disclose synthetic media', 'platform', 'high',
     'PARTIALLY_VERIFIED', '{"rule":"AI-generated content must be disclosed where required"}'::jsonb)
ON CONFLICT (key) DO NOTHING;
