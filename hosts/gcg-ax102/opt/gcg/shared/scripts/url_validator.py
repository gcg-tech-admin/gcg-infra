#!/usr/bin/env python3
"""
URL Validation Gate — GCG Data Ingest Pipeline
================================================
Validates URLs before they enter public.sources or public.staging_dta_agreements.

Two layers:
  1. Format gate  — scheme + domain + non-empty path (instant, no network)
  2. HTTP gate    — HEAD (fallback GET) with 5 s timeout; accepts 2xx/3xx

Batch API uses ThreadPoolExecutor capped at MAX_CONCURRENT (default 50).

Usage (CLI):
  python3 url_validator.py https://example.com
  python3 url_validator.py --batch url1 url2 url3
  python3 url_validator.py --audit               # scan all HEALTHY sources, mark DEAD
  python3 url_validator.py --audit --dry-run

Usage (Python):
  from url_validator import validate_url, validate_urls_batch
  from url_validator import safe_insert_source, safe_insert_staging_dta

  # Single
  ok, status, err = validate_url("https://example.com")

  # Batch
  results = validate_urls_batch(["https://a.com", "https://b.com"])

  # Safe DB insert — raises URLValidationError on bad URL
  safe_insert_source(conn, subject_type="company", subject_id=42,
                     url="https://example.com", authority_tier="T1")
"""

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMEOUT_S = 5
MAX_CONCURRENT = 50
ACCEPT_STATUS = set(range(200, 400))   # 2xx and 3xx

_URL_RE = re.compile(
    r'^(https?|ftp)://'           # scheme
    r'([a-zA-Z0-9\-._~!$&\'()*+,;=:@%]+)'   # host
    r'(:\d+)?'                    # optional port
    r'(/[^\s]*)?$',               # path (optional but validated separately)
    re.IGNORECASE,
)

_USER_AGENT = (
    "Mozilla/5.0 (compatible; GCG-Ingest/1.0; +https://globalcapitalgroup.com)"
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class URLValidationError(ValueError):
    """Raised when a URL fails format or HTTP validation."""
    def __init__(self, url: str, reason: str):
        self.url = url
        self.reason = reason
        super().__init__(f"URL validation failed: {reason} — {url}")


# ---------------------------------------------------------------------------
# Session factory (per-thread)
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _USER_AGENT})
    # One retry on connection errors only — not on HTTP errors (we want the real code)
    adapter = HTTPAdapter(max_retries=Retry(total=1, connect=1, read=0, backoff_factor=0.3))
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


# ---------------------------------------------------------------------------
# Format validation (no network)
# ---------------------------------------------------------------------------

def _validate_format(url: str) -> Optional[str]:
    """Return error string if format is bad, else None."""
    if not url or not url.strip():
        return "empty URL"
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "ftp"):
        return f"invalid or missing scheme '{parsed.scheme}'"
    if not parsed.netloc:
        return "missing domain / host"
    if "." not in parsed.netloc.lstrip("["):   # skip IPv6 check
        return f"domain '{parsed.netloc}' has no TLD"
    if len(url) < 10:
        return "URL too short (likely truncated)"
    # Detect obvious truncation: ends mid-word without a valid path terminator
    if url.endswith(("//", "http:", "https:", "ftp:", "www.", "http:/", "https:/")):
        return "URL appears truncated"
    return None


# ---------------------------------------------------------------------------
# HTTP validation (single URL)
# ---------------------------------------------------------------------------

def validate_url(
    url: str,
    timeout: int = TIMEOUT_S,
    session: Optional[requests.Session] = None,
) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Validate a single URL.

    Returns (ok: bool, http_status: int|None, error: str|None).
    ok=True only when HTTP status is 2xx or 3xx.
    """
    fmt_err = _validate_format(url)
    if fmt_err:
        return False, None, fmt_err

    own_session = session is None
    if own_session:
        session = _make_session()

    try:
        try:
            resp = session.head(
                url, timeout=timeout, allow_redirects=True
            )
        except requests.exceptions.Timeout:
            return False, None, f"timed out after {timeout}s"
        except requests.exceptions.SSLError as e:
            return False, None, f"SSL error: {e}"
        except requests.exceptions.ConnectionError as e:
            return False, None, f"connection error: {e}"
        except Exception as e:
            return False, None, f"request error: {e}"

        # Some servers return 405 Method Not Allowed for HEAD — retry with GET
        if resp.status_code == 405:
            try:
                resp = session.get(
                    url, timeout=timeout, allow_redirects=True, stream=True
                )
                resp.close()
            except Exception as e:
                return False, None, f"GET fallback error: {e}"

        code = resp.status_code
        if code in ACCEPT_STATUS:
            return True, code, None
        return False, code, f"HTTP {code} {resp.reason}"

    finally:
        if own_session:
            session.close()


# ---------------------------------------------------------------------------
# Batch validation
# ---------------------------------------------------------------------------

def validate_urls_batch(
    urls: List[str],
    max_concurrent: int = MAX_CONCURRENT,
    timeout: int = TIMEOUT_S,
) -> Dict[str, Tuple[bool, Optional[int], Optional[str]]]:
    """
    Validate multiple URLs concurrently.

    Returns dict: url -> (ok, http_status, error)
    Thread pool is capped at max_concurrent (default 50).
    """
    results: Dict[str, Tuple[bool, Optional[int], Optional[str]]] = {}
    unique_urls = list(dict.fromkeys(urls))   # deduplicate, preserve order

    workers = min(max_concurrent, len(unique_urls)) if unique_urls else 1

    def _check(url: str) -> Tuple[str, bool, Optional[int], Optional[str]]:
        session = _make_session()
        try:
            ok, status, err = validate_url(url, timeout=timeout, session=session)
            return url, ok, status, err
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_check, u): u for u in unique_urls}
        for future in as_completed(futures):
            url, ok, status, err = future.result()
            results[url] = (ok, status, err)

    return results


# ---------------------------------------------------------------------------
# DB helpers — safe insert wrappers
# ---------------------------------------------------------------------------

def safe_insert_source(
    conn,
    subject_type: str,
    subject_id: int,
    url: str,
    authority_tier: str = "T3",
    cadence_days: int = 30,
    acquired_by: Optional[str] = None,
    skip_http_check: bool = False,
) -> int:
    """
    Validate url then INSERT into public.sources.

    Raises URLValidationError on bad URL.
    Returns inserted/upserted source id.
    """
    url = (url or "").strip()
    fmt_err = _validate_format(url)
    if fmt_err:
        raise URLValidationError(url, fmt_err)

    if not skip_http_check:
        ok, status, err = validate_url(url)
        if not ok:
            raise URLValidationError(url, err or "unknown HTTP error")

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO public.sources
            (subject_type, subject_id, url, authority_tier, cadence_days, acquired_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (subject_type, subject_id, url)
        DO UPDATE SET
            authority_tier = EXCLUDED.authority_tier,
            cadence_days   = EXCLUDED.cadence_days,
            updated_at     = now()
        RETURNING id
        """,
        (subject_type, subject_id, url, authority_tier, cadence_days, acquired_by),
    )
    row = cur.fetchone()
    return row[0]


def safe_insert_staging_dta(
    conn,
    row_data: dict,
    skip_http_check: bool = False,
) -> int:
    """
    Validate URL fields in row_data then INSERT into public.staging_dta_agreements.

    Checks: scraped_from_url, treaty_source_url, source_folder_url.
    source_file_path is a local path or Drive URL — format-validated only.

    Raises URLValidationError on first failure.
    Returns staging_id.
    """
    url_fields_http = ["scraped_from_url", "treaty_source_url", "source_folder_url"]
    url_fields_format = ["source_file_path"]

    # Format-only fields
    for field in url_fields_format:
        val = (row_data.get(field) or "").strip()
        if val:
            fmt_err = _validate_format(val)
            if fmt_err:
                raise URLValidationError(val, f"[{field}] {fmt_err}")

    # HTTP-checked fields
    if not skip_http_check:
        urls_to_check = [
            (f, row_data[f].strip())
            for f in url_fields_http
            if row_data.get(f) and row_data[f].strip()
        ]
        if urls_to_check:
            batch_results = validate_urls_batch([u for _, u in urls_to_check])
            for field, url in urls_to_check:
                ok, status, err = batch_results[url]
                if not ok:
                    raise URLValidationError(url, f"[{field}] {err}")

    cur = conn.cursor()
    columns = list(row_data.keys())
    placeholders = ", ".join(["%s"] * len(columns))
    col_list = ", ".join(columns)
    values = [row_data[c] for c in columns]

    cur.execute(
        f"INSERT INTO public.staging_dta_agreements ({col_list}) VALUES ({placeholders}) RETURNING staging_id",
        values,
    )
    row = cur.fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Audit — scan existing sources, mark DEAD
# ---------------------------------------------------------------------------

def audit_sources(
    conn,
    dry_run: bool = False,
    batch_size: int = 200,
    verbose: bool = True,
) -> Dict[str, int]:
    """
    Scan all HEALTHY sources, mark DEAD if URL is unreachable.

    Returns counts: {checked, dead, skipped, errors}
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT id, url FROM public.sources WHERE source_health = 'HEALTHY' ORDER BY id"
    )
    rows = cur.fetchall()

    counts = {"checked": 0, "dead": 0, "skipped": 0, "errors": 0}
    dead_ids = []

    # Process in batches
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        url_map = {url: sid for sid, url in batch}
        results = validate_urls_batch(list(url_map.keys()))

        for url, (ok, status, err) in results.items():
            sid = url_map[url]
            counts["checked"] += 1
            if not ok:
                counts["dead"] += 1
                dead_ids.append(sid)
                if verbose:
                    print(f"  DEAD  id={sid}  {err}  {url}")
            elif verbose and (i + 1) % 50 == 0:
                print(f"  OK    id={sid}  HTTP {status}  {url}")

    if dead_ids and not dry_run:
        update_cur = conn.cursor()
        update_cur.execute(
            "UPDATE public.sources SET source_health='DEAD', updated_at=now() WHERE id = ANY(%s)",
            (dead_ids,),
        )
        conn.commit()
        if verbose:
            print(f"\nMarked {len(dead_ids)} source(s) as DEAD.")
    elif dead_ids and dry_run:
        if verbose:
            print(f"\n[DRY RUN] Would mark {len(dead_ids)} source(s) as DEAD.")

    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli():
    parser = argparse.ArgumentParser(
        description="GCG URL Validation Gate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("urls", nargs="*", help="URLs to validate")
    parser.add_argument(
        "--batch", action="store_true", help="Validate multiple URLs concurrently"
    )
    parser.add_argument(
        "--audit", action="store_true",
        help="Audit all HEALTHY sources in DB and mark DEAD ones",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="With --audit: report only, do not update DB",
    )
    parser.add_argument(
        "--timeout", type=int, default=TIMEOUT_S, help="HTTP timeout in seconds"
    )
    args = parser.parse_args()

    if args.audit:
        import os
        os.environ.setdefault("GCG_DB_HOST", "10.0.0.2")
        sys.path.insert(0, "/opt/gcg/shared/gcg_tools")
        from db_connect import get_connection   # noqa
        conn = get_connection(admin=True)
        print(f"Auditing HEALTHY sources {'[DRY RUN]' if args.dry_run else ''}...")
        t0 = time.time()
        counts = audit_sources(conn, dry_run=args.dry_run)
        elapsed = time.time() - t0
        print(
            f"\nDone in {elapsed:.1f}s — "
            f"checked={counts['checked']} dead={counts['dead']} "
            f"skipped={counts['skipped']}"
        )
        conn.close()
        sys.exit(1 if counts["dead"] > 0 else 0)

    if not args.urls:
        parser.print_help()
        sys.exit(1)

    if len(args.urls) == 1 and not args.batch:
        url = args.urls[0]
        ok, status, err = validate_url(url, timeout=args.timeout)
        if ok:
            print(f"OK  HTTP {status}  {url}")
            sys.exit(0)
        else:
            print(f"FAIL  {err}  {url}")
            sys.exit(1)

    # Batch mode
    results = validate_urls_batch(args.urls, timeout=args.timeout)
    exit_code = 0
    for url, (ok, status, err) in results.items():
        if ok:
            print(f"OK    HTTP {status}  {url}")
        else:
            print(f"FAIL  {err}  {url}")
            exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    _cli()
