-- Daily usage analysis over Thunder Forge JSONL logs.
-- Required parameter: period (YYYY-MM-DD), set via:
--   .param set period 2026-06-02

-- 1) requests by user
with request_rows as (
    select
        cast(timestamp as timestamp) as ts,
        client_id,
        regexp_extract(olla_endpoint, '^([^-]+)-omlx-', 1) as node_id,
        model,
        cast(latency_ms as bigint) as latency_ms,
        coalesce(
            cast(total_tokens as bigint),
            cast(prompt_tokens as bigint) + cast(completion_tokens as bigint),
            0
        ) as tokens
    from read_json_auto('logs/tf-edge-access.jsonl', ignore_errors=true)
    where cast(timestamp as date) = cast($period as date)
)
select
    client_id,
    count(*) as requests,
    sum(latency_ms) as consumed_ms,
    sum(tokens) as tokens
from request_rows
group by client_id
order by requests desc, client_id;

-- 2) requests by node
with request_rows as (
    select
        cast(timestamp as timestamp) as ts,
        client_id,
        regexp_extract(olla_endpoint, '^([^-]+)-omlx-', 1) as node_id,
        model,
        cast(latency_ms as bigint) as latency_ms,
        coalesce(
            cast(total_tokens as bigint),
            cast(prompt_tokens as bigint) + cast(completion_tokens as bigint),
            0
        ) as tokens
    from read_json_auto('logs/tf-edge-access.jsonl', ignore_errors=true)
    where cast(timestamp as date) = cast($period as date)
)
select
    node_id,
    count(*) as requests,
    sum(latency_ms) as consumed_ms,
    sum(tokens) as tokens
from request_rows
where node_id <> ''
group by node_id
order by requests desc, node_id;

-- 3) requests by hour with model and user split
with request_rows as (
    select
        cast(timestamp as timestamp) as ts,
        client_id,
        regexp_extract(olla_endpoint, '^([^-]+)-omlx-', 1) as node_id,
        model,
        cast(latency_ms as bigint) as latency_ms,
        coalesce(
            cast(total_tokens as bigint),
            cast(prompt_tokens as bigint) + cast(completion_tokens as bigint),
            0
        ) as tokens
    from read_json_auto('logs/tf-edge-access.jsonl', ignore_errors=true)
    where cast(timestamp as date) = cast($period as date)
),
hour_counts as (
    select
        strftime(ts, '%H') as hour,
        count(*) as requests,
        sum(latency_ms) as consumed_ms
    from request_rows
    group by strftime(ts, '%H')
),
model_agg as (
    select
        hour,
        string_agg(model || ':' || cardinality, ', ' order by cardinality desc, model) as by_model
    from (
        select strftime(ts, '%H') as hour, model, count(*) as cardinality
        from request_rows
        where model <> ''
        group by strftime(ts, '%H'), model
    )
    group by hour
),
user_agg as (
    select
        hour,
        string_agg(client_id || ':' || cardinality, ', ' order by cardinality desc, client_id) as by_user
    from (
        select strftime(ts, '%H') as hour, client_id, count(*) as cardinality
        from request_rows
        group by strftime(ts, '%H'), client_id
    )
    group by hour
)
select
    h.hour,
    h.requests,
    h.consumed_ms,
    m.by_model,
    u.by_user
from hour_counts h
left join model_agg m using (hour)
left join user_agg u using (hour)
order by h.hour;

-- 4) requests by node and hour bucket
with request_rows as (
    select
        cast(timestamp as timestamp) as ts,
        client_id,
        regexp_extract(olla_endpoint, '^([^-]+)-omlx-', 1) as node_id,
        model,
        cast(latency_ms as bigint) as latency_ms,
        coalesce(
            cast(total_tokens as bigint),
            cast(prompt_tokens as bigint) + cast(completion_tokens as bigint),
            0
        ) as tokens
    from read_json_auto('logs/tf-edge-access.jsonl', ignore_errors=true)
    where cast(timestamp as date) = cast($period as date)
)
select
    node_id,
    strftime(ts, '%H') as hour,
    count(*) as requests
from request_rows
where node_id <> ''
group by node_id, strftime(ts, '%H')
order by node_id, hour;

-- 5) requests by model
with request_rows as (
    select
        cast(timestamp as timestamp) as ts,
        client_id,
        regexp_extract(olla_endpoint, '^([^-]+)-omlx-', 1) as node_id,
        model,
        cast(latency_ms as bigint) as latency_ms,
        coalesce(
            cast(total_tokens as bigint),
            cast(prompt_tokens as bigint) + cast(completion_tokens as bigint),
            0
        ) as tokens
    from read_json_auto('logs/tf-edge-access.jsonl', ignore_errors=true)
    where cast(timestamp as date) = cast($period as date)
)
select
    model,
    count(*) as requests,
    sum(latency_ms) as consumed_ms,
    sum(tokens) as tokens
from request_rows
where model <> ''
group by model
order by requests desc, model;

-- 6) node hot-loaded model summary from snapshots
select
    node_id,
        count(*) as samples,
        round(avg(cast(hot_loaded_count as double)), 2) as hot_loaded_avg,
        max(cast(hot_loaded_count as bigint)) as hot_loaded_max
from read_json_auto('logs/tf-node-metrics.jsonl', ignore_errors=true)
where cast(timestamp as date) = cast($period as date)
group by node_id
order by node_id;