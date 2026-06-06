-- URL Validation Gate — DB Layer (format checks only)
-- ======================================================
-- Applies to: public.sources (url), public.staging_dta_agreements (source_file_path)
--
-- NOTE: HTTP reachability (404/timeout detection) is handled by the Python layer
-- (url_validator.py). PostgreSQL cannot make outbound HTTP calls without pg_net,
-- which is not installed. These triggers enforce URL *format* — they are the last
-- line of defence against truncated/malformed URLs that slip past the Python gate.
--
-- Deploy: psql -h 10.0.0.2 -U gcg_admin -d gcg_intelligence -f url_validator.sql


-- ---------------------------------------------------------------------------
-- 1. URL format validation function
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_validate_url_format(
    p_url   text,
    p_field text DEFAULT 'url'
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_scheme text;
BEGIN
    -- Must not be null or empty
    IF p_url IS NULL OR trim(p_url) = '' THEN
        RAISE EXCEPTION 'URL validation failed: % is empty or NULL', p_field;
    END IF;

    -- Must start with http:// or https:// (ftp accepted)
    IF p_url !~ '^(https?|ftp)://' THEN
        RAISE EXCEPTION
            'URL validation failed: % missing valid scheme (got: %) — expected http:// or https://',
            p_field, left(p_url, 30);
    END IF;

    -- Must have a domain with a dot (rules out bare hostnames / typos)
    IF p_url !~ '^https?://[^/]*\.[^/]' AND p_url !~ '^ftp://[^/]*\.[^/]' THEN
        RAISE EXCEPTION
            'URL validation failed: % has no TLD in domain — URL appears malformed: %',
            p_field, left(p_url, 80);
    END IF;

    -- Detect obvious truncation patterns
    IF p_url ~ '(https?://|ftp://)$'
    OR p_url ~ '//\s*$'
    OR right(trim(p_url), 4) IN ('http', 'ftp:', 'www.')
    THEN
        RAISE EXCEPTION
            'URL validation failed: % appears truncated: %',
            p_field, left(p_url, 80);
    END IF;

    -- Minimum plausible length: scheme + "://" + 4-char domain + "." + tld
    IF length(trim(p_url)) < 11 THEN
        RAISE EXCEPTION
            'URL validation failed: % is suspiciously short (%s chars) — likely truncated: %',
            p_field, length(trim(p_url)), p_url;
    END IF;
END;
$$;

COMMENT ON FUNCTION fn_validate_url_format(text, text) IS
  'Format-validates a URL string. Raises EXCEPTION on empty, missing scheme, missing TLD, or truncation.';


-- ---------------------------------------------------------------------------
-- 2. Trigger function — public.sources (url column)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION trg_fn_sources_url_validate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Only fire on INSERT or when url actually changes on UPDATE
    IF (TG_OP = 'INSERT') OR (TG_OP = 'UPDATE' AND NEW.url IS DISTINCT FROM OLD.url) THEN
        PERFORM fn_validate_url_format(NEW.url, 'sources.url');
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION trg_fn_sources_url_validate() IS
  'BEFORE INSERT/UPDATE trigger body for public.sources — validates url format.';


-- ---------------------------------------------------------------------------
-- 3. Trigger — public.sources
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_sources_url_format ON public.sources;

CREATE TRIGGER trg_sources_url_format
    BEFORE INSERT OR UPDATE OF url
    ON public.sources
    FOR EACH ROW
    EXECUTE FUNCTION trg_fn_sources_url_validate();

COMMENT ON TRIGGER trg_sources_url_format ON public.sources IS
  'Format-validates sources.url on INSERT and UPDATE. HTTP reachability is validated upstream in Python (url_validator.py).';


-- ---------------------------------------------------------------------------
-- 4. Trigger function — public.staging_dta_agreements (URL columns)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION trg_fn_staging_dta_url_validate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE') THEN
        -- source_file_path: may be a local path or Drive URL — only validate if it looks like a URL
        IF NEW.source_file_path IS NOT NULL
           AND NEW.source_file_path ~ '^https?://'
           AND (TG_OP = 'INSERT' OR NEW.source_file_path IS DISTINCT FROM OLD.source_file_path)
        THEN
            PERFORM fn_validate_url_format(NEW.source_file_path, 'staging_dta_agreements.source_file_path');
        END IF;

        -- scraped_from_url
        IF NEW.scraped_from_url IS NOT NULL
           AND (TG_OP = 'INSERT' OR NEW.scraped_from_url IS DISTINCT FROM OLD.scraped_from_url)
        THEN
            PERFORM fn_validate_url_format(NEW.scraped_from_url, 'staging_dta_agreements.scraped_from_url');
        END IF;

        -- treaty_source_url
        IF NEW.treaty_source_url IS NOT NULL
           AND (TG_OP = 'INSERT' OR NEW.treaty_source_url IS DISTINCT FROM OLD.treaty_source_url)
        THEN
            PERFORM fn_validate_url_format(NEW.treaty_source_url, 'staging_dta_agreements.treaty_source_url');
        END IF;

        -- source_folder_url
        IF NEW.source_folder_url IS NOT NULL
           AND (TG_OP = 'INSERT' OR NEW.source_folder_url IS DISTINCT FROM OLD.source_folder_url)
        THEN
            PERFORM fn_validate_url_format(NEW.source_folder_url, 'staging_dta_agreements.source_folder_url');
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION trg_fn_staging_dta_url_validate() IS
  'BEFORE INSERT/UPDATE trigger body for public.staging_dta_agreements — validates all URL columns.';


-- ---------------------------------------------------------------------------
-- 5. Trigger — public.staging_dta_agreements
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_staging_dta_url_format ON public.staging_dta_agreements;

CREATE TRIGGER trg_staging_dta_url_format
    BEFORE INSERT OR UPDATE OF source_file_path, scraped_from_url, treaty_source_url, source_folder_url
    ON public.staging_dta_agreements
    FOR EACH ROW
    EXECUTE FUNCTION trg_fn_staging_dta_url_validate();

COMMENT ON TRIGGER trg_staging_dta_url_format ON public.staging_dta_agreements IS
  'Format-validates URL columns on INSERT/UPDATE. HTTP check done upstream in Python.';


-- ---------------------------------------------------------------------------
-- 6. Verification queries (run after deploy to confirm)
-- ---------------------------------------------------------------------------

-- Should list both new triggers:
-- SELECT trigger_name, event_object_table, event_manipulation
-- FROM information_schema.triggers
-- WHERE trigger_name IN ('trg_sources_url_format','trg_staging_dta_url_format');

-- Quick smoke test (should raise exceptions):
-- SELECT fn_validate_url_format('https://gov.uk/vat', 'test');           -- OK
-- SELECT fn_validate_url_format('https://', 'test');                      -- FAIL: truncated
-- SELECT fn_validate_url_format('https://www.', 'test');                  -- FAIL: no TLD
-- SELECT fn_validate_url_format('not-a-url', 'test');                     -- FAIL: no scheme
