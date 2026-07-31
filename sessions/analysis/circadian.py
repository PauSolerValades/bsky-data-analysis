"""Check circadian rhythm: hourly session-start density by language.

Usage:
    uv run sessions/analysis/circadian.py --table-name sessions_tukey_k1_5
    uv run sessions/analysis/circadian.py --table-name sessions_tukey_k1_5 --plot-dir ../hyperparameter/plots/tukey/k1_5
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env")
from running_locally.local_db import Where, get_connection as local_connect

WHERE = Where.from_env()
TBL_PREFIX = "pau_db." if WHERE == Where.SERVER else ""

# ── Thesis styling ───────────────────────────────────────────────────────
sns.set_theme(style="whitegrid")
plt.rcParams.update({
    "text.usetex": False,
    "axes.labelsize": 11,
    "font.size": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 8,
})

DEFAULT_OUT = Path(__file__).resolve().parent / "results"

# Languages with concentrated timezones → UTC offset
LANGUAGES = ["de", "ja", "ko", "pt", "fr", "es"]
TZ_OFFSET = {"de": 1, "ja": 9, "ko": 9, "pt": -3, "fr": 1, "es": -3}
PALETTE = sns.color_palette("colorblind", n_colors=len(LANGUAGES))


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Circadian rhythm check.")
    parser.add_argument("--table-name", type=str, required=True)
    parser.add_argument("--plot-dir", type=str, default=str(DEFAULT_OUT),
                        help="Output directory for the plot")
    args = parser.parse_args()
    plot_dir = Path(args.plot_dir)
    plot_dir.mkdir(parents=True, exist_ok=True)

    conn = local_connect(Where.from_env(), repo_root=str(REPO))

    # Per-DID dominant language from posts
    print("  Computing per-user dominant language...", file=sys.stderr)
    lang_rows = conn.query("""
        WITH user_langs AS (
            SELECT did, lang, COUNT(*) AS n
            FROM bsky.posts
            WHERE lang IS NOT NULL AND lang != ''
            GROUP BY did, lang
        ),
        ranked AS (
            SELECT did, lang,
                   ROW_NUMBER() OVER (PARTITION BY did ORDER BY n DESC) AS rn
            FROM user_langs
        )
        SELECT did, lang FROM ranked WHERE rn = 1
    """)
    did_lang = {r[0]: r[1] for r in lang_rows}
    print(f"    {len(did_lang):,} users with language labels", file=sys.stderr)

    # Session starts with DID
    rows = conn.query(f"""
        SELECT did, session_start FROM {TBL_PREFIX}{args.table_name}
    """)
    conn.close()

    # Bucket by local hour per language
    local_hours = {lang: np.zeros(24, dtype=np.float64) for lang in LANGUAGES}
    total_sessions = {lang: 0 for lang in LANGUAGES}

    for did, session_start in rows:
        lang = did_lang.get(did)
        if lang not in TZ_OFFSET:
            continue
        offset = TZ_OFFSET[lang]
        utc_hour = (session_start / 1_000_000 / 3600) % 24
        local_hour = int((utc_hour + offset) % 24)
        local_hours[lang][local_hour] += 1
        total_sessions[lang] += 1

    # Normalize to density
    for lang in LANGUAGES:
        if total_sessions[lang] > 0:
            local_hours[lang] /= total_sessions[lang]

    # ── Print ──────────────────────────────────────────────────────────

    print(f"\n── Circadian check: {args.table_name} ──")
    print(f"  {'Language':<6s} {'sessions':>10s} {'peak hour (local)':>18s}  "
          f"{'night ratio':>12s}")
    print(f"  {'-'*52}")
    for lang in LANGUAGES:
        n = total_sessions[lang]
        if n == 0:
            continue
        peak = np.argmax(local_hours[lang])
        night_ratio = local_hours[lang][2:6].sum()
        print(f"  {lang:<6s} {n:>10,}  {peak:>02d}:00 local            "
              f"{night_ratio:>11.3f}")

    # ── Plot ───────────────────────────────────────────────────────────

    fig, ax = plt.subplots(figsize=(10, 5))

    for i, lang in enumerate(LANGUAGES):
        if total_sessions[lang] == 0:
            continue
        hours = np.arange(24)
        density = local_hours[lang]
        ax.plot(hours, density, color=PALETTE[i], linewidth=1.5,
                label=f"{lang} ({total_sessions[lang]:,})")
        ax.fill_between(hours, 0, density, color=PALETTE[i], alpha=0.1)

    ax.set_xlabel("Local hour of day")
    ax.set_ylabel("Session-start density")
    ax.set_title(f"Circadian rhythm — {args.table_name}",
                 fontsize=12, fontweight="bold")
    ax.set_xticks(range(0, 24, 3))
    ax.legend()
    ax.set_xlim(0, 23)

    ax.axvspan(0, 6, color="gray", alpha=0.06)
    ax.axvspan(22, 24, color="gray", alpha=0.06)

    fig.tight_layout()
    path = plot_dir / f"circadian__{args.table_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → saved {path}")


if __name__ == "__main__":
    main()
