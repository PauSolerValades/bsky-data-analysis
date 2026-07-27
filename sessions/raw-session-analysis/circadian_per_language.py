"""
§6 — Circadian analysis per language.

Plots session density by local hour for mono-country languages
(ja→JST, de→CET, ko→KST).  5-minute bin resolution.

Validates check #3: sessions must follow human waking hours per timezone.
"""

import sys

import matplotlib.pyplot as plt
import numpy as np

from _common import (
    Source,
    get_connection,
    savefig,
    OUT,
)

# Language → UTC offset.  Only mono-country languages with clean timezone mapping.
LANG_OFFSETS = {
    "ja": 9,    # Japan (JST = UTC+9)
    "de": 1,    # Germany (CET = UTC+1)
    "ko": 9,    # Korea (KST = UTC+9)
    "en": -5,   # US East (EST = UTC-5) — dominant US timezone
}

SECONDS_PER_HOUR = 3600
US_PER_HOUR = 3_600_000_000


def run(source: Source):
    """Produce §6 plots for a single source."""
    print(f"\n── §6: Circadian per language — {source.value} ──", file=sys.stderr)

    conn = get_connection()

    # Fetch sessions with user language, real sessions only (duration > 0)
    sql = f"""
        SELECT s.session_start, u.primary_lang
        FROM {source.table} s
        JOIN pau_db.users u ON s.did = u.did
        WHERE s.duration_s > 0
          AND u.primary_lang IN ('ja', 'de', 'ko', 'en')
    """
    print(f"  Fetching {source.table} with language join ...", file=sys.stderr)
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    conn.close()
    print(f"    → {len(rows):,} sessions", file=sys.stderr)

    # Group by language
    from collections import defaultdict
    lang_hours: dict[str, list[float]] = defaultdict(list)

    for start_us, lang in rows:
        offset = LANG_OFFSETS.get(lang)
        if offset is None:
            continue
        local_hour = ((int(start_us) / 1_000_000 / SECONDS_PER_HOUR) + offset) % 24
        lang_hours[lang].append(local_hour)

    for lang in ["ja", "de", "ko", "en"]:
        hours = np.array(lang_hours.get(lang, []), dtype=np.float64)
        if len(hours) < 100:
            print(f"    {lang}: {len(hours):,} sessions — skipping", file=sys.stderr)
            continue

        offset = LANG_OFFSETS[lang]
        label = f"{lang} (UTC{offset:+d})"
        print(f"    {lang}: {len(hours):,} sessions", file=sys.stderr)

        # ── 5-minute bin histogram ──
        fig, ax = plt.subplots(figsize=(14, 5))
        ax.hist(hours, bins=24 * 12, range=(0, 24), color="#4A90D9",
                alpha=0.85, edgecolor="white", linewidth=0.2)
        ax.set_xlabel(f"Local hour ({label})")
        ax.set_ylabel("Sessions")
        ax.set_title(f"{source.label}\nCircadian rhythm — {label}")
        ax.set_xticks(range(0, 24, 2))
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        savefig(fig, f"06_{source.value}_{lang}_circadian_hist.png")

        # ── KDE density ──
        from scipy.stats import gaussian_kde
        fig, ax = plt.subplots(figsize=(14, 5))
        padded = np.concatenate([hours - 24, hours, hours + 24])
        kde = gaussian_kde(padded, bw_method=0.3)
        xs = np.linspace(0, 24, 300)
        ax.fill_between(xs, kde(xs), alpha=0.3, color="#4A90D9")
        ax.plot(xs, kde(xs), "-", color="#4A90D9", linewidth=2)
        ax.set_xlabel(f"Local hour ({label})")
        ax.set_ylabel("Density")
        ax.set_title(f"{source.label}\nKDE density — {label}")
        ax.set_xticks(range(0, 24, 2))
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        savefig(fig, f"06_{source.value}_{lang}_circadian_kde.png")

        # ── Print peak ──
        peak_h = np.argmax(np.bincount(np.clip(hours.astype(int), 0, 23)))
        print(f"      Peak: {peak_h:02d}:00 local", file=sys.stderr)
