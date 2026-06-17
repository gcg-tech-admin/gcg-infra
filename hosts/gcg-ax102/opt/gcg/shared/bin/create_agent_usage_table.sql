CREATE TABLE IF NOT EXISTS public.agent_usage (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    agent           TEXT NOT NULL,
    seat_profile    TEXT,
    human_id        TEXT,
    human_name      TEXT,
    channel         TEXT,
    direction       TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    session_id      TEXT,
    msg_len_bucket  TEXT CHECK (msg_len_bucket IS NULL OR msg_len_bucket IN ('xs', 's', 'm', 'l', 'xl')),
    workflow_class  TEXT,
    source          TEXT NOT NULL CHECK (source IN ('backfill', 'live')),
    src_uuid        TEXT UNIQUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_usage_ts ON public.agent_usage(ts);
CREATE INDEX IF NOT EXISTS idx_agent_usage_agent ON public.agent_usage(agent);
CREATE INDEX IF NOT EXISTS idx_agent_usage_source ON public.agent_usage(source);
CREATE INDEX IF NOT EXISTS idx_agent_usage_direction ON public.agent_usage(direction);

GRANT ALL ON public.agent_usage TO gcg_talos;
GRANT USAGE, SELECT ON SEQUENCE public.agent_usage_id_seq TO gcg_talos;
GRANT ALL ON public.agent_usage TO gcg_daen;
GRANT USAGE, SELECT ON SEQUENCE public.agent_usage_id_seq TO gcg_daen;
GRANT ALL ON public.agent_usage TO gcg_vulcan;
GRANT USAGE, SELECT ON SEQUENCE public.agent_usage_id_seq TO gcg_vulcan;
