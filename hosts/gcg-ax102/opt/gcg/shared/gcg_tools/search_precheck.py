#!/opt/gcg/shared/venv/bin/python3
"""
search_precheck.py — v4: Usefulness-weighted + source-quality + provider routing + query-similarity guard.

CANONICAL VERSION (lives in gcg_tools/). scripts/ version is a thin CLI wrapper.

v4 adds:
  - Fix #1: Unify with scripts/ (this is canonical)
  - Fix #2: conn.commit() after upsert block
  - Fix #3: AGENT_SLOT_MAP with 29 real agents, no phantoms
  - Fix #4: pg_trgm index eligible domain lookup (no seq scan)
  - Fix #5: Query-similarity guard — skip only when cosine sim >= 0.7
"""

import argparse
import json
import logging
import re
import sys
import os
from datetime import datetime, timezone
from typing import Optional

try:
    import tldextract
except ImportError:
    tldextract = None

log = logging.getLogger("search_precheck")

# ═══════════════════════════════════════════════════════════════════════
# Fix #3: AGENT_SLOT_MAP — 29 real fleet agents (from fleet-roster.yaml)
# Map rule: each agent_name → [agent_name], EXCEPT talos → ['main']
# NO phantom entries (janus, knossos, hera, athena removed)
# ═══════════════════════════════════════════════════════════════════════
AGENT_SLOT_MAP = {
    "talos": ["main"],
    "daen": ["daen"],
    "nik": ["nik"],
    "varys": ["varys"],
    "vulcan": ["vulcan"],
    "alex": ["alex"],
    "alexa": ["alexa"],
    "algaib": ["algaib"],
    "angela": ["angela"],
    "anna": ["anna"],
    "argus": ["argus"],
    "bob": ["bob"],
    "chiron": ["chiron"],
    "goku": ["goku"],
    "hector": ["hector"],
    "jc": ["jc"],
    "kenji": ["kenji"],
    "kira": ["kira"],
    "leon": ["leon"],
    "malik": ["malik"],
    "marcus": ["marcus"],
    "max": ["max"],
    "mnemosyne": ["mnemosyne"],
    "niccolo": ["niccolo"],
    "phil": ["phil"],
    "tom": ["tom"],
    "vera": ["vera"],
    "viktor": ["viktor"],
    "yuri": ["yuri"],
}

USEFULNESS_SKIP = frozenset({"used", "proceeded"})

# ── Hardcoded rejected-domain fallback (for domains not yet in sources table) ──
REJECTED_DOMAINS = frozenset({
    "emirabiz.com",
    "taxconsultantsindubai.com",
    "bestaxca.com",
})

# ── Query-shape classification for provider routing (Increment 3b) ──

FACTUAL_PATTERNS = re.compile(
    r"^(how many|what is|when was|where is|who is|how much|what are|who are|"
    r"what does|what do|what was|what were|define|tell me about)\b",
    re.IGNORECASE,
)

DB_LOOKUP_PATTERNS = re.compile(
    r"\b(fee|cost|price|charge|rate|license|permit|renewal|"
    r"zone|freezone|free zone|dmcc|jafza|dubai south|dubai world|"
    r"shams|dwc|dafza|dso|dtec|rak ia|mecca|"
    r"deadline|requirement|document|regulation)\b",
    re.IGNORECASE,
)

DEEP_PATTERNS = re.compile(
    r"\b(compare|comparison|analyze|analysis|differences|pros? cons?|"
    r"research|latest|trends|trending|detailed|comprehensive|"
    r"in-depth|deep dive|overview|summary of|evaluate|assessment)\b",
    re.IGNORECASE,
)

_DOMAIN_CACHE = {}


def classify_query(query: str) -> str:
    """
    Classify a query by shape → recommend the best tool.

    Returns one of: 'web_search', 'db_lookup', 'gcg_research'
    """
    if not query:
        return "web_search"

    # URL / domain lookup → db_lookup
    if re.match(r"https?://", query.strip()) or re.match(
        r"^[\w-]+(\.[\w-]+)+\b", query.strip()
    ):
        return "db_lookup"

    # Deep/in-depth queries → gcg_research (Perplexity Sonar)
    if DEEP_PATTERNS.search(query):
        return "gcg_research"

    # Simple factual lookups → web_search
    if FACTUAL_PATTERNS.match(query):
        return "web_search"

    # DB/specific data → db_lookup
    if DB_LOOKUP_PATTERNS.search(query):
        return "db_lookup"

    # Default conservative → web_search
    return "web_search"


# ── Domain extraction helpers (Increment 3) ──

def extract_domain(url: str) -> Optional[str]:
    """Extract eTLD+1 (registrable domain) from a URL string."""
    if not url:
        return None
    cache_key = url[:256]
    if cache_key in _DOMAIN_CACHE:
        return _DOMAIN_CACHE[cache_key]
    try:
        if tldextract:
            extracted = tldextract.extract(url)
            if extracted.domain and extracted.suffix:
                result = f"{extracted.domain}.{extracted.suffix}".lower()
                _DOMAIN_CACHE[cache_key] = result
                return result
        # Fallback: naive extraction
        m = re.search(r"https?://([^/]+)", url)
        if m:
            host = m.group(1).lower()
            parts = host.split(".")
            if len(parts) >= 2:
                result = ".".join(parts[-2:])
                _DOMAIN_CACHE[cache_key] = result
                return result
        _DOMAIN_CACHE[cache_key] = None
        return None
    except Exception:
        return None


def extract_domains_from_tool_input(tool_input) -> list[str]:
    """Extract all URLs from tool_input JSONB and return their eTLD+1 domains."""
    if not tool_input:
        return []
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except json.JSONDecodeError:
            d = extract_domain(tool_input)
            return [d] if d else []

    domains = set()
    if isinstance(tool_input, dict):
        for val in tool_input.values():
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                d = extract_domain(val)
                if d:
                    domains.add(d)
        url = tool_input.get("url")
        if url:
            d = extract_domain(url)
            if d:
                domains.add(d)
    return list(domains)


# ── Source-quality helpers (Increment 3) ──

_TIER_RANK = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}


def lookup_domain_quality(domains: list[str], cur) -> dict[str, dict]:
    """
    Lookup domain quality from sources table + hardcoded fallback.

    sources.url stores FULL urls (https://host/path), so we match on the
    registrable domain (eTLD+1) extracted from each url — not the raw string.

    Uses pg_trgm index on url for fast ILIKE matching (Fix #4).

    Returns dict: {domain: {tier, is_rejected, score, found_in_db}}
    """
    result = {}
    if not domains:
        return result

    targets = set(domains)
    db_lookup: dict[str, dict] = {}
    try:
        patterns = [f"%{d}%" for d in targets]
        cur.execute(
            "SELECT url, authority_tier, is_rejected, score FROM sources WHERE url ILIKE ANY(%s)",
            (patterns,),
        )
        for url, tier, is_rejected, score in cur.fetchall():
            dom = extract_domain(url)
            if dom not in targets:
                continue
            sc = float(score) if score is not None else None
            cur_info = db_lookup.get(dom)
            if cur_info is None:
                db_lookup[dom] = {"tier": tier, "is_rejected": bool(is_rejected),
                                  "score": sc, "found_in_db": True}
                continue
            cur_info["is_rejected"] = cur_info["is_rejected"] or bool(is_rejected)
            if tier and _TIER_RANK.get(tier, 9) < _TIER_RANK.get(cur_info["tier"], 9):
                cur_info["tier"] = tier
            if sc is not None and (cur_info["score"] is None or sc > cur_info["score"]):
                cur_info["score"] = sc
    except Exception as e:
        log.warning("sources domain lookup failed: %s", e)
        db_lookup = {}

    for domain in domains:
        if domain in db_lookup:
            result[domain] = db_lookup[domain]
        elif domain in REJECTED_DOMAINS:
            # Fallback: hardcoded reject list → upsert into sources for future lookups
            url_with_scheme = f"https://{domain}"
            try:
                cur.execute(
                    """
                    INSERT INTO sources (url, authority_tier, is_rejected, subject_type, subject_id, cadence_days, source_health, created_at, updated_at)
                    VALUES (%s, 'T3', TRUE, 'domain', 0, 7, 'DEAD', NOW(), NOW())
                    ON CONFLICT (subject_type, subject_id, url) DO UPDATE SET is_rejected = TRUE, authority_tier = 'T3', updated_at = NOW()
                    """,
                    (url_with_scheme,),
                )
            except Exception as e:
                log.warning("Failed to upsert rejected domain %s: %s", domain, e)
            result[domain] = {"tier": "T3", "is_rejected": True, "score": 0.0, "found_in_db": False}
        else:
            result[domain] = {"tier": None, "is_rejected": False, "score": None, "found_in_db": False}

    return result


# ── Query-similarity cache (Fix #5) ──

_EMBED_CACHE = {}


def _get_embedding(text: str) -> Optional[list[float]]:
    """Get embedding, cached in-memory per call."""
    if text in _EMBED_CACHE:
        return _EMBED_CACHE[text]
    try:
        sys.path.insert(0, "/opt/gcg/shared/gcg_tools")
        from gcg_embed import embed_text
        emb = embed_text(text)
        _EMBED_CACHE[text] = emb
        return emb
    except (ImportError, Exception) as e:
        log.debug("embed_text unavailable: %s", e)
        return None


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── DB helpers ──────────────────────────────────────────────────────────

def get_conn():
    sys.path.insert(0, "/opt/gcg/shared/gcg_tools")
    from db_connect import get_connection
    return get_connection(admin=True)


def fmt_age(ts) -> str:
    if not ts:
        return "unknown"
    now = datetime.now(timezone.utc)
    delta = now - ts
    days = delta.days
    hours = delta.seconds // 3600
    mins = (delta.seconds % 3600) // 60
    if days > 7:
        return f"{days // 7}w{days % 7}d"
    if days > 0:
        return f"{days}d{hours}h"
    if hours > 0:
        return f"{hours}h{mins}m"
    return f"{mins}m"


# ── Precheck logic ──────────────────────────────────────────────────────

def precheck(
    agent_name: str,
    query_text: str,
    match_threshold: float = 0.5,
    memory_limit: int = 5,
    tool_call_hours: int = 72,
    tool_call_limit: int = 10,
) -> dict:
    """
    Run a precheck for agent conversation context.

    Returns:
        {
            "matches_found": bool,
            "sources": [...],
            "freshness": str,
            "recommendation": "skip" | "proceed",
            "recommended_tool": str,
            "memory_matches": int,
            "tool_calls": int,
            "dead_ends": [...],
            "skippable_searches": int,
            "query_similarity": float | None,    # Fix #5
            "detail": {...}
        }
    """
    result = {
        "matches_found": False,
        "sources": [],
        "freshness": None,
        "recommendation": "proceed",
        "recommended_tool": classify_query(query_text),
        "memory_matches": 0,
        "tool_calls": 0,
        "dead_ends": [],
        "avoid_domains": [],
        "bad_source_count": 0,
        "weak_query_count": 0,
        "skippable_searches": 0,
        "query_similarity": None,
        "detail": None,
    }

    conn = get_conn()
    cur = conn.cursor()
    newest_ts = None

    try:
        # ── (a) pgvector recall over memories table ──
        embedding = _get_embedding(query_text)

        if embedding:
            embed_json = json.dumps(embedding)
            sql = """
                SELECT content, created_at,
                       1 - (embedding <=> %s::halfvec) AS sim
                FROM memories
                WHERE agent_name = %s
                  AND is_active = true
                  AND 1 - (embedding <=> %s::halfvec) >= %s
                ORDER BY sim DESC
                LIMIT %s
            """
            cur.execute(sql, (embed_json, agent_name, embed_json, match_threshold, memory_limit))
        else:
            sql = """
                SELECT content, created_at, 1.0 AS sim
                FROM memories
                WHERE agent_name = %s
                  AND is_active = true
                  AND content ILIKE %s
                ORDER BY created_at DESC
                LIMIT %s
            """
            cur.execute(sql, (agent_name, f"%{query_text}%", memory_limit))

        mem_rows = cur.fetchall()
        memory_matches = len(mem_rows)
        result["memory_matches"] = memory_matches

        if mem_rows:
            result["sources"].append("memories")
            for row in mem_rows:
                if row[1] and (newest_ts is None or row[1] > newest_ts):
                    newest_ts = row[1]
            result["detail"] = {
                "memory_hits": [
                    {"content": r[0][:200], "similarity": round(float(r[2]), 3), "age": fmt_age(r[1])}
                    for r in mem_rows[:3]
                ]
            }

        # ── (b) tool_call_log lookup with usefulness weighting + source quality ──
        tc_agents = AGENT_SLOT_MAP.get(agent_name, [agent_name])
        placeholders = ", ".join(["%s"] * len(tc_agents))

        sql_tc = f"""
            SELECT outcome, usefulness, usefulness_confidence,
                   result_summary, error_text, ts, tool_input
            FROM tool_call_log
            WHERE agent_name IN ({placeholders})
              AND ts >= NOW() - INTERVAL '%s hours'
              AND tool_name IN ('web_search', 'web_fetch', 'search', 'query')
            ORDER BY ts DESC
            LIMIT %s
        """
        cur.execute(sql_tc, tuple(tc_agents) + (tool_call_hours, tool_call_limit))
        tc_rows = cur.fetchall()

        result["tool_calls"] = len(tc_rows)

        if tc_rows:
            result["sources"].append("tool_call_log")
            if tc_rows[0][5] and (newest_ts is None or tc_rows[0][5] > newest_ts):
                newest_ts = tc_rows[0][5]

            skippable = 0
            dead_ends = []
            highest_similarity = 0.0
            prior_skip_queries = []

            # First pass: collect domains + prior skip queries for similarity check
            for row in tc_rows:
                outcome, usefulness, confidence, summary, err, ts, tool_input = row
                u = usefulness or "unclassified"

                if u in USEFULNESS_SKIP:
                    skippable += 1
                    # ── Fix #5: collect prior skip query for similarity check ──
                    if isinstance(tool_input, dict) and "query" in tool_input:
                        prior_skip_queries.append((tool_input["query"], ts))
                    continue

                if u in ("refined", "abandoned", "empty", "error", "unclassified"):
                    domains = extract_domains_from_tool_input(tool_input)
                    domain_quality = lookup_domain_quality(domains, cur)

                    bad_domains = [
                        d for d, info in domain_quality.items()
                        if info.get("tier") == "T3" or info.get("is_rejected")
                    ]
                    weak_domains = [
                        d for d, info in domain_quality.items()
                        if info.get("tier") and info["tier"] != "T3" and not info.get("is_rejected")
                    ]

                    if bad_domains:
                        reason = "bad_source"
                        recommendation = "seek T0-T2 source"
                        avoid = bad_domains
                    elif domains and not domain_quality:
                        reason = "weak_query"
                        recommendation = "re-search refine query"
                        avoid = []
                    elif weak_domains:
                        reason = "weak_query"
                        recommendation = "re-search refine query"
                        avoid = []
                    elif domains and all(
                        not info.get("tier") and not info.get("is_rejected")
                        for info in domain_quality.values()
                    ):
                        reason = "weak_query"
                        recommendation = "re-search refine query"
                        avoid = []
                    else:
                        reason = "weak_query"
                        recommendation = "re-search refine query"
                        avoid = []

                    dead_end = {
                        "outcome": outcome,
                        "usefulness": u,
                        "confidence": float(confidence) if confidence else None,
                        "age": fmt_age(ts),
                        "reason": reason,
                        "recommendation": recommendation,
                    }
                    if avoid:
                        dead_end["avoid_domains"] = avoid
                    if summary:
                        dead_end["result_summary"] = summary[:200]
                    if err:
                        dead_end["error"] = err[:200]
                    dead_ends.append(dead_end)

            bad_source_count = sum(1 for d in dead_ends if d.get("reason") == "bad_source")
            weak_query_count = sum(1 for d in dead_ends if d.get("reason") == "weak_query")
            result["bad_source_count"] = bad_source_count
            result["weak_query_count"] = weak_query_count

            result["skippable_searches"] = skippable
            result["dead_ends"] = dead_ends[:5]

            # ── Fix #5: Query-similarity guard ──
            # Only skip if there are prior useful searches AND the current query
            # is semantically similar (cosine sim >= 0.7) to at least one.
            if skippable >= 1 and prior_skip_queries:
                current_emb = _get_embedding(query_text)
                if current_emb:
                    for prior_query, prior_ts in prior_skip_queries:
                        prior_emb = _get_embedding(prior_query)
                        if prior_emb:
                            sim = _cosine_sim(current_emb, prior_emb)
                            highest_similarity = max(highest_similarity, sim)
                            if sim >= 0.7:
                                break

                result["query_similarity"] = round(highest_similarity, 3)

                if highest_similarity >= 0.7:
                    result["recommendation"] = "skip"
                else:
                    # Query is different enough — proceed even though there are prior useful searches
                    result["recommendation"] = "proceed"
                    result["skip_blocked_reason"] = (
                        f"query_similarity={highest_similarity:.3f} < 0.7 threshold; "
                        "query is semantically different from prior useful searches"
                    )
            elif skippable >= 1 and not prior_skip_queries:
                # No prior queries to compare — conservative skip
                result["recommendation"] = "skip"
            else:
                result["recommendation"] = "proceed"

            # Bad-source guidance (overrides skip if bad sources found)
            bad_source_dead_ends = [d for d in dead_ends if d.get("reason") == "bad_source"]
            if bad_source_dead_ends:
                result["recommendation"] = "proceed"
                avoid_all = sorted({d for de in bad_source_dead_ends for d in de.get("avoid_domains", [])})
                result["avoid_domains"] = avoid_all
                result["source_guidance"] = "seek T0-T2 source; do not re-pull the avoided domains"

            # ── (c) Detail ──
            if not result.get("detail"):
                result["detail"] = {}

            useful_rows = [r for r in tc_rows if (r[1] or "") in USEFULNESS_SKIP]
            if useful_rows:
                useful_list = []
                for r in useful_rows[:3]:
                    entry = {
                        "outcome": r[0],
                        "usefulness": r[1],
                        "age": fmt_age(r[5]),
                    }
                    if r[3]:
                        entry["summary"] = r[3][:200]
                    useful_list.append(entry)
                result["detail"]["useful_prior_searches"] = useful_list

            if dead_ends:
                result["detail"]["dead_ends_tried"] = dead_ends[:3]

        # ── Aggregate ──
        result["matches_found"] = len(result["sources"]) > 0
        result["freshness"] = fmt_age(newest_ts) if newest_ts else "no data"

        # ═══════════════════════════════════════════════════════════════
        # Fix #2: conn.commit() — persist upserted rejected domains
        # ═══════════════════════════════════════════════════════════════
        conn.commit()

        return result

    finally:
        cur.close()
        conn.close()


# ── CLI ─────────────────────────────────────────────────────────────────

def cli():
    parser = argparse.ArgumentParser(
        description="Agent-conversation memory precheck v4 — usefulness + source-quality + routing + similarity guard"
    )
    parser.add_argument("agent", help="Agent name (e.g. talos, daen, varys)")
    parser.add_argument("query", help="Query text to check against memories")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--memory-limit", type=int, default=5)
    parser.add_argument("--tool-call-hours", type=int, default=72)
    parser.add_argument("--tool-call-limit", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    r = precheck(
        agent_name=args.agent,
        query_text=args.query,
        match_threshold=args.threshold,
        memory_limit=args.memory_limit,
        tool_call_hours=args.tool_call_hours,
        tool_call_limit=args.tool_call_limit,
    )

    if args.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        status = "✅ MATCH" if r["matches_found"] else "❌ NO MATCH"
        rec = r.get("recommendation", "proceed")
        rec_icon = "⏭️" if rec == "skip" else "🔍"
        tool = r.get("recommended_tool", "web_search")
        print(f"{status} | freshness={r['freshness']} | sources={r['sources']}")
        print(f"  recommendation={rec_icon} {rec} | recommended_tool={tool}")
        print(f"  memory_matches={r['memory_matches']} | recent_tool_calls={r['tool_calls']}")
        qs = r.get("query_similarity")
        if qs is not None:
            print(f"  query_similarity={qs} (threshold=0.7)")
        deads = r.get("dead_ends", [])
        if deads:
            bs = r.get("bad_source_count", 0)
            wq = r.get("weak_query_count", 0)
            print(f"  dead_ends_tried: {len(deads)} ({bs} bad_source, {wq} weak_query)")
            for d in deads[:3]:
                reason_sigil = "🔴" if d.get("reason") == "bad_source" else "⚠️"
                avoid = ""
                if d.get("avoid_domains"):
                    avoid = f" AVOID: {', '.join(d['avoid_domains'])}"
                print(f"    {reason_sigil} [{d['outcome']}/{d['usefulness']}] ({d['age']}) {d.get('error', d.get('result_summary', ''))[:100]}...{avoid}")
        if r.get("detail") and r["detail"].get("memory_hits"):
            for h in r["detail"]["memory_hits"]:
                print(f"  → [{h['similarity']}] ({h['age']}) {h['content'][:120]}...")


if __name__ == "__main__":
    cli()
