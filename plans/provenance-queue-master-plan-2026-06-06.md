# Master Plan — GCG Data Provenance Queue ("the burn-down board")

> Requested by Peter (#4403–#4405, 2026-06-06). Scoped by Varys. Arch + Council: Daen. Build: Talos. QA: Vulcan. Owner: Peter.

## Goal (north star)
**Every number GCG advises a client on is traceable to a verified, current, authoritative source — and we know the moment it goes stale.** Not "we have data" — *trusted* data: value + source + freshness all agree. Success = one board that shows, for every entity (zone, DTA, law), its provenance state and next action, and that board burns down to green.

Success criteria (measurable):
- One queryable view classifies **100% of entities** (56 freezones + 152 DTAs + 718 laws + 45 gov fees) into a provenance state.
- Each non-green entity has a **next action** + **owner** + **cadence**.
- Staleness is **detected automatically** (cadence_days vs last_fetched_at), not by memory.

## Live baseline (pulled 2026-06-06 — the gap we're closing)
| Domain | Entities | Sourced | Verified/scored | Gap |
|---|---|---|---|---|
| Freezone pricing | 56 zones (20 w/ fee rows, 1,483 rows) | 642/1,483 rows linked (43%) | ~7 zones file-verified | ~36 zones zero-data; 841 rows unsourced |
| DTAs | 152 | 14 sourced (9%) | 3 scored (UK/RU/FR pilot) | 138 unsourced |
| Legislation (FTA/federal) | 718 | **0 sourced** | 0 | entire table no provenance |
| Gov fees | 45 | 12 registered | 10 Vulcan-scored | backfill |
| Source registry | 85 sources | — | 34 scored | 51 unscored |

## The provenance state machine (one per entity)
```
no_data → unsourced → sourced_unscored → scored → verified_current
                                              ↓ (cadence elapsed / source changed)
                                          scored_stale → (re-verify) → verified_current
```
| State | Meaning | Next action | Run type |
|---|---|---|---|
| `no_data` | entity exists, no values | extract from authority | **new run** |
| `unsourced` | values exist, no source link | open real doc, reconcile, link | **re-run (verify)** |
| `sourced_unscored` | linked but not rubric-scored | score (Varys advisory → Vulcan binding) | score |
| `scored` | scored, below verified bar | corroborate / promote per band | autopromote |
| `verified_current` | value=doc, scored≥gate, within cadence | none until cadence elapses | — |
| `scored_stale` | cadence_days elapsed OR source hash changed | re-verify against latest doc | **re-run (cadence)** |

## Design — REUSE, do not reinvent (RED LINE #4)
Already on disk, wire together — do NOT build new:
- **`sources`** already carries `subject_type`/`subject_id` (entity link), `cadence_days` (set on all 85), `last_fetched_at` (only 5 populated — gap), `source_health`, `score`, `is_rejected`, `authority_tier`. This is the provenance spine.
- **`freshness_scan_log`** (scan_date, source_snapshot_hash, superseded_count, stale_count) — a freshness scanner table **already exists but is unused**. Revive/extend it, don't make a new one.
- **Queue infra exists:** `dispatch_queue`, `follow_up_queue`, `peter_tap_queue`, `lightrag_ingest_queue`. Route actions through these.
- **Autopromote bands** (🟢≥90 AUTO / 🟡70–89 PETER-TAP / 🟠 HOLD / 🔴 REJECT) + **search_precheck** (don't re-research) + the **reverification ledger** + **SOURCE_DATABASE.md**.

**The actual deliverable = a VIEW + a scanner, not a new pipeline:**
1. `provenance_status` VIEW: each entity (freezones ∪ dta_agreements ∪ legislation ∪ gov_fees) LEFT JOIN `sources` → computes `state` (above) + `next_action` + days-to-stale (`cadence_days` − age(`last_fetched_at`)).
2. Freshness scanner (extend `freshness_scan_log`): nightly, flips `verified_current` → `scored_stale` when cadence elapsed or `content_hash` changes; enqueues re-runs.
3. The board = `SELECT state, count(*) FROM provenance_status GROUP BY state` + per-entity drill-down. Burns down as states go green.

## Phases
| Phase | Goal | Owner | Gate |
|---|---|---|---|
| **P1** Visibility | `provenance_status` view live; every entity classified | Talos build / Varys spec | Council: view returns 56+152+718+45 rows, no NULL state |
| **P2** Freshness | extend `freshness_scan_log` scanner; populate `last_fetched_at`; auto-flag stale | Talos | scanner runs nightly, stale count non-zero & correct |
| **P3** Burn-down: pricing | 36 zero-data zones (new run) + 841 unsourced rows (re-run) | Varys extract → Talos stage | each zone → ≥`sourced_unscored` |
| **P4** Burn-down: tax/legal | 138 DTAs + 718 laws sourced (mostly new run vs MoF/FTA/gazette) | Varys → Talos | DTAs/laws → `sourced` |
| **P5** Score + promote | score the 51 unscored sources; promote per band | Varys advisory + Vulcan binding | gate-banded, no unscored linked source |

## Rerun vs new-run logic (plain)
- **new run** = state `no_data` → nothing to reconcile, go extract.
- **re-run (verify)** = state `unsourced` → data exists, find+open the doc, reconcile every value, link+score.
- **re-run (cadence)** = state `scored_stale` → was good, cadence elapsed/source changed, re-pull latest and reconcile.
- precheck guards all three: never re-research what's already `verified_current` and in-cadence.

## Open questions for Daen
1. View vs materialized table for `provenance_status`? (718+ legislation + joins — matview refreshed by the scanner is likely cheaper than a live view.)
2. Entity union across 4 tables with different PKs — single view with `entity_type`+`entity_id`, or per-domain views + a roll-up? Recommend single normalized view.
3. Default `cadence_days` per domain (fees ~365, legislation event-driven not time, DTAs ~730)? Legislation freshness = `content_hash` change, not a clock — confirm.
4. Does the unused `freshness_scan_log` have an owning service already (even if dormant) before I assume it's free to extend?

## Ownership
Scope: Varys. Arch + Council convening + QA gate: Daen. Build: Talos. Binding scores: Vulcan. Owner/approver: Peter. Consumer: Varys + Marcus (advisory reads the board).
