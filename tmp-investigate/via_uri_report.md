# `via_uri` — origin, meaning, and its use in structural virality

Investigation date: 2026-08-17. Evidence in `via_scan.py` (scans raw firehose JSONL).

## What `via_uri` is and where it comes from

It's the **`via` field of the raw AT Protocol record**, extracted verbatim by the
ingestion code that built `bsky.records`. Nothing is computed:

- Raw firehose JSONL: `commit.record.via` is a strongRef `{uri, cid}`
- Ingestion → `bsky.records`: `record.via.uri` → `via_uri`, `record.via.cid` → `via_cid`
  (same pattern as `record.subject` → `subject_uri` / `subject_cid`)

Verified against `/data/nfs/datasets/bluesky/firehose/non-posts/2026-04/11/records_20260411_00.jsonl`,
e.g. a like carrying `"via": {"uri": "at://did:plc:.../app.bsky.feed.repost/3mj6jll3pla2s", ...}`.

## What it means

`via` records **how the user discovered the thing they acted on** — pointer attribution
set by the client. From a full-hour scan of the 2026-04-11 firehose:

| Collection | `via_uri` points to | Meaning |
|---|---|---|
| `app.bsky.feed.like` | `app.bsky.feed.repost` (229K in 1h) | user liked the post because they saw it through someone's repost |
| `app.bsky.feed.repost` | `app.bsky.feed.repost` (55K) | user reposted after seeing someone else's repost — a repost-of-repost chain link |
| `app.bsky.graph.follow` | `app.bsky.graph.starterpack` (14K) | user followed the account via a starter pack |

It is **optional** in the lexicon (set by the official app when the view can be
attributed), so most records lack it: ~20% of likes, ~32% of reposts, ~14% of follows
carry one (matches NULL rates in the `records.parquet` sample).

### Caveats

1. **2026-era data only.** The `via` field does not exist in the 2025-04 firehose
   files (zero occurrences) — it was added to the lexicon later. Any time-series using
   it must start from when clients began emitting it.
2. **Only in `bsky.records`, not `bsky.posts`.** Posts carry `reply_root_uri` instead
   — a different concept (thread structure vs. discovery attribution).
3. **Missingness is not random.** Non-official clients never set `via`, so absence
   mixes "genuinely direct view" with "attribution lost" (client-correlated).

## Is it used appropriately in the structural-virality pipeline?

Chain reviewed: `cascade-creation/01_dump_reposts.sql` → `build_cascades` (parent
resolution, `go/cascade.go:90-97`) → `StructuralVirality()` (`cascade-metrics/go/cascade.go:133`).

### Correct ✅

1. **Formula.** `ν = 2·W / (n(n−1))` with W = Wiener index via the subtree-crossing
   edge sum — exactly Goel et al. (2015) structural virality (mean pairwise distance).
   The O(N) CSR implementation is right.
2. **Tree semantics.** Root = original post author, children = reposts, parent from
   `via_uri` when present, fallback to root. Attaching to root is the only defensible
   fallback, since "no via" legitimately includes genuinely direct reposts.
3. **Reposts only.** Tree built from `feed.repost` creates, not likes — ν is defined
   on reshare cascades. Like-level `via` is correctly unused.
4. **Ordering.** `ORDER BY subject_uri, time_us, is_repost` with creation-first
   tie-break guarantees the root exists before children attach.

### Caveats to handle

1. **ν is a floor, not an estimate.** ~68% of reposts have no `via` → flatten onto the
   root → structural virality is systematically underestimated, more so for genuinely
   viral cascades (long chains are what gets hidden). Fine if the thesis states it as
   a lower bound.
2. **Window edge effects.** Reposts whose `via` points before the dump window miss
   their parent → root fallback. Posts born near the window end have right-censored
   cascades (missing later reposts → size and ν underestimated). Fix: only analyze
   posts created in the first N days of the window.
3. **⚠️ No size threshold exists yet.** Goel et al. restrict to cascades with ≥100
   adopters: ν is a deterministic artifact for tiny trees (n=2 → ν=2 always). The
   29M-row `cascades.parquet` is dominated by tiny cascades and nothing filters them —
   the analysis must filter `size >= 100` (or ≥50) or the ν distribution is meaningless.
4. **Minor:** dump is `operation='create'` only, so reposts later deleted still count
   as adoptions. Matches Goel et al.'s convention — just don't call them "currently
   visible reposts" in the text.

### Bottom line

Pipeline design and formula are correct, and `via_uri` is used for exactly what the
field exists for (parent edges distinguishing broadcast from viral spread). State the
lower-bound caveat in the thesis; add the `size >= 100` filter and early-window
restriction at analysis time — neither exists yet.
