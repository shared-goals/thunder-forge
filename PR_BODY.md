## Problem

The usage section advertised data that does not exist. It promised a per-user, per-node and per-model **token** breakdown, but the access record has no token fields at all: they were removed from the record and `stream_options.include_usage` is never requested, so even a streaming response's upstream `usage` is thrown away. An operator reading that list would reasonably conclude a cost or throughput report was one `usage report` away.

A related trap in the same paragraph: `consumed_ms` reads like model time. It is wall time at the edge, and for a streaming request it ends when the last byte reaches the client, so it includes client backpressure.

## Change

- Drops the "tokens when present" / "tokens" claims from the three summary-dimension lines.
- States plainly that token counts and a first-token timestamp are not collected, and points at the existing **Minimal Metric Strategy** section that specifies what would have to be collected to get them.
- States that `consumed_ms` is wall time at the edge, including client backpressure on streaming requests.

Documentation only. The strategy section already describes `time_to_first_token_ms` as a field to collect, so it needs no correction — the correction is that the report does not have it yet.

## Verification

`git diff` touches one paragraph in `README.md`. No code, no tests, no behaviour. Not verified by running anything, because nothing runs differently.

## Limitations

This makes the documentation honest rather than closing the gap: measuring speed and cost still needs token extraction and a first-chunk timestamp in the edge, which is a separate decision.
