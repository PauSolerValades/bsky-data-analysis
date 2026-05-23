# Twitter / X session analytics replication — Bluesky adaptation

> ⛔ **This document describes a deprecated pipeline** (`user_core_events` →
> `user_core_events_dominant` → `sessions_threshold`). The methodology
> (Kooti et al. 2016 elbow-method threshold detection) is valid, but the
> actual tables were built from a buggy SQL filter. **Superseded by:**
>
> | Old | New |
> |-----|-----|
> | `pau_db.user_core_events` | `pau_db.all_events` / `pau_db.engaged_events` |
> | `pau_db.user_core_events_dominant` | `pau_db.engaged_events` (no separate dominant table needed) |
> | `pau_db.sessions_threshold` (fixed 265 s) | `pau_db.sessions_engagement` (Tukey IQR) |
> | `pau_db.sessions_tukey` (old IQR) | `pau_db.sessions_all` + `pau_db.sessions_engagement` |
>
> **Current pipeline docs:**
> - Source tables: `docs/DATABASE.md` → `all_events` / `engaged_events`
> - Session tables: `docs/DATABASE.md` → `sessions_all` / `sessions_engagement`
> - EDA: `docs/sessions/eda.md`
> - Distribution fitting: `docs/sessions/analysis/distribution-fitting.md`
> - Method comparison: `docs/sessions/tukey-vs-threshold.md`
>
> Kept for archival reference only. Do not regenerate the old tables.

---

## Original methodology (archived)

The original pipeline replicated the Twitter session analytics study
(Kooti et al., SocInfo 2016) with Bluesky firehose data. Key steps:

1. **Event mapping** — `bsky.posts` (top-level + replies) + `bsky.records` (reposts) → `user_core_events`
2. **Filtering** — removed tourists (≤5 events) and bots (≥501 events). Identified the 101–500 stratum as the dominant gap contributor (37.4% of inter-arrival gaps).
3. **Elbow detection** — Kneedle algorithm on the dominant stratum gap histogram → Δt = 265 s (4.4 min).
4. **Session clustering** — applied 265 s threshold to all events from the dominant stratum → `pau_db.sessions_threshold` (8.47M sessions).
5. **Per-user IQR** — adaptive Tukey thresholds for the 6–500 human range → `pau_db.sessions_tukey` (~29.3M sessions).

**Why it was deprecated:** The SQL that built `user_core_events` from
`bsky.records` and `bsky.posts` had an incorrect filter. The error
propagated through the entire pipeline. The methodology itself is sound
and was reapplied correctly to `all_events` / `engaged_events`.

---

*Archived 2026-05-23. Original version documented to 2026-05-17.*
