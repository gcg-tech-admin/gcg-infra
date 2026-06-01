# Event Verification — Phase 0.1

**Date:** 2026-06-02
**Status:** ❌ `session:end` absent, ✅ `session:compact:after` confirmed available

## Event Audit

| Event | Available | Used By |
|-------|-----------|---------|
| `session:end` | ❌ Not in OpenClaw event catalog | N/A |
| `command:stop` | ✅ User `/stop` | Not reliable — user may not issue it |
| `session:compact:after` | ✅ Yes | compaction-capture hook (production since 2026-04) |
| `gateway:shutdown` | ✅ Yes | Session drain for gateway restart — too granular |

## Decision

Use **`session:compact:after`** as the trigger event for fix extraction.

**Rationale:**
1. Session compaction naturally marks the end of a meaningful work block (session is being summarized/concluded)
2. Already proven in production by compaction-capture hook — event fires reliably with full session context
3. Session JSONL is still available at this point (compaction creates the summary, files are intact)
4. Hook already has reference implementation (`/opt/gcg/shared/hooks/compaction-capture/`)

**Payload shape** (from compaction-capture handler):
- `event.type`: `"session"`
- `event.action`: `"compact:after"`
- `event.sessionKey`: `"agent:main:<provider>:<uuid>"`
- `event.context`: `{ compactedCount, summaryLength, tokensBefore, tokensAfter }`

**Recalibration note:** Phase 0 calibration thresholds (precision ≥ 0.85, recall ≥ 0.80) remain valid. Extraction occurs after session end, not during, which is more conservative than the original design.
