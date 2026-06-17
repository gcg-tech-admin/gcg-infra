-- agent_usage_weekly: Rollup view producing 6 EOS metrics.
-- Consumed by the Sunday brief job (weekly-gcg-eos-brief-robin).
-- Build: Talos, 2026-06-16.

CREATE OR REPLACE VIEW public.agent_usage_weekly AS
WITH week_cutoff AS (
    SELECT date_trunc('week', NOW()) - INTERVAL '7 days' AS wk_start
),
weekly_usage AS (
    SELECT * FROM public.agent_usage, week_cutoff
    WHERE ts >= week_cutoff.wk_start
      AND agent != '__unknown__'
),
-- Metric 1: Interactions per agent
interactions_per_agent AS (
    SELECT agent, COUNT(*) AS interaction_count
    FROM weekly_usage
    GROUP BY agent
),
-- Metric 2: Active humans per agent (distinct human_id or human_name)
active_humans_per_agent AS (
    SELECT agent, COUNT(DISTINCT COALESCE(human_id, human_name, seat_profile)) AS human_count
    FROM weekly_usage
    WHERE direction = 'inbound'
    GROUP BY agent
),
-- Metric 3: Top workflow per agent (mode)
ranked_workflows AS (
    SELECT agent, workflow_class, COUNT(*) AS wf_count,
           ROW_NUMBER() OVER (PARTITION BY agent ORDER BY COUNT(*) DESC) AS rn
    FROM weekly_usage
    WHERE workflow_class IS NOT NULL
    GROUP BY agent, workflow_class
),
top_workflow_per_agent AS (
    SELECT agent, workflow_class AS top_workflow, wf_count AS top_workflow_count
    FROM ranked_workflows WHERE rn = 1
),
-- Metric 4: Workflow mix across fleet
workflow_mix AS (
    SELECT workflow_class, COUNT(*) AS wf_count,
           ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS wf_pct
    FROM weekly_usage
    WHERE workflow_class IS NOT NULL
    GROUP BY workflow_class
),
-- Metric 5: Dormant agents (assigned, ~0 use)
-- Cross-reference the known agent list from seat_human_map + fleet roster
all_known_agents AS (
    SELECT unnest(ARRAY[
        'daen','talos','vulcan','argus','varys','mnemosyne',
        'leon','algaib','kenji','malik','alexa','alex','viktor',
        'max','nik','bob','anna','vera','marcus','jc','angela',
        'goku','socrates','niccolo','cassandra','confucius',
        'nemesis','chiron','hector','tom','phil','wonhoo','yuri','kira'
    ]) AS agent
),
agent_activity AS (
    SELECT agent, COUNT(*) AS interaction_count
    FROM weekly_usage
    GROUP BY agent
),
dormant_agents AS (
    SELECT a.agent, COALESCE(ua.interaction_count, 0) AS interaction_count
    FROM all_known_agents a
    LEFT JOIN agent_activity ua ON a.agent = ua.agent
    WHERE COALESCE(ua.interaction_count, 0) = 0
),
-- Metric 6: Founder-concentration ratio (% interactions with Peter vs team)
peter_interactions AS (
    SELECT COUNT(*) AS peter_count
    FROM weekly_usage
    WHERE seat_profile = 'sub-peter'
       OR human_id = '418059105'
       OR human_name ILIKE '%peter%'
),
total_interactions AS (
    SELECT COUNT(*) AS total_count FROM weekly_usage
)

-- Final output: all 6 metrics as rows for easy consumption
SELECT 'interactions_per_agent' AS metric, agent AS dimension, interaction_count::TEXT AS value
FROM interactions_per_agent
UNION ALL
SELECT 'active_humans_per_agent', agent, human_count::TEXT
FROM active_humans_per_agent
UNION ALL
SELECT 'top_workflow_per_agent', agent, top_workflow || ' (' || top_workflow_count || ')' 
FROM top_workflow_per_agent
UNION ALL
SELECT 'workflow_mix', workflow_class, wf_count || ' (' || wf_pct || '%)'
FROM workflow_mix
UNION ALL
SELECT 'dormant_agent', agent, '0'
FROM dormant_agents
UNION ALL
SELECT 'founder_concentration_ratio', 'peter_pct', 
       ROUND(100.0 * (SELECT peter_count FROM peter_interactions) / 
             NULLIF((SELECT total_count FROM total_interactions), 0), 1)::TEXT
UNION ALL
SELECT 'founder_concentration_ratio', 'total_weekly',
       (SELECT total_count FROM total_interactions)::TEXT;
