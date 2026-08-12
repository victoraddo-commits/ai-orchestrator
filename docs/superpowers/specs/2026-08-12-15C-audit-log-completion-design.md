# 15C — Platform Audit Log Completion

**Date**: 2026-08-12
**Phase**: 15C
**Status**: approved
**Priority**: 52

## Overview

Complete the existing `/audit` endpoint by wiring 5 additional data sources
that were omitted from the initial build, adding real client IP attribution,
and adding test coverage.

## What exists already

`/audit` (GET) at `core/api.py:736` merges 4 sources into a chronological
feed with JSON + CSV export and filtering by actor, source, action, and date
range.  `core/audit_aggregator.py` provides a parallel module with the same
mapping logic plus `extract_client_ip()`.

## What we add

### 1. Five new data sources

| Source key | Memory file | Record accessor | Action prefix |
|-----------|-------------|-----------------|---------------|
| `gateway_audit` | `gateway_audit.json` | `.requests` (list) | `gateway.{status_code}` |
| `secret_access_audit` | `secret_access_audit.json` | raw list | `secret.{action}` |
| `ai_usage_history` | `ai_usage_history.json` | `.records` (list) | `ai.delegate` |
| `remediation_history` | `remediation_history.json` | `.records` (list) | `remediation.{action}` |
| `verification_history` | `verification_history.json` | `.records` (list) | `verification.{action}` |

Each gets a normalizer function (same pattern as the existing 4 normalizers)
and an entry in `AUDIT_SOURCES` and `_NORMALIZERS`.

### 2. Real client IP

Pass `request: Request` through to each normalizer so they can call
`extract_client_ip()` on `x-forwarded-for` / `x-real-ip`.  Currently every
entry shows `127.0.0.1`.

### 3. Minimal auth gating

The endpoint currently has no auth check.  Use the existing JWT auth
(`_verify_token_or_raise`) with `role >= "viewer"` to gate read access.

### 4. Tests

New file `tests/test_audit.py` covering:
- All 9 sources produce entries in merged feed
- Filtering by actor, source, action produces correct subsets
- CSV export has correct columns and content
- Date range filtering works
- 401 returned when unauthenticated

## What we do NOT add

- Write endpoint attribution (15A's scope)
- Full role-based permissions (15A's scope)
- New storage files
- Changes to `core/audit_aggregator.py` (use the existing inline normalizers
  in `core/api.py` instead — the aggregator module is a parallel
  implementation, keep it but don't add new sources to it.)

## Files changed

| File | Change |
|------|--------|
| `core/api.py` | Add 5 sources + normalizers, pass request for real IP, add auth check |
| `tests/test_audit.py` | New: test merged feed, filtering, CSV export, date range, auth |
| `roadmap.json` | Mark 15C as `completed` |
