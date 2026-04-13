"""
Tennis 2024 data pipeline — Wimbledon backtesting
=================================================
Pulls Jeff Sackmann match CSVs (ATP/WTA), cleans and merges years, computes
Elo snapshots through a pre–Wimbledon 2024 cutoff, and exports feature tables.

Sources: github.com/JeffSackmann/tennis_atp, tennis_wta (CC BY-NC-SA 4.0).

Outputs under ./tennis_data/:
  - atp_matches_all_clean.csv, wta_matches_all_clean.csv
  - all_tours_matches_clean.csv          (ATP + WTA, `tour` column)
  - atp_matches_2024.csv, wta_matches_2024.csv
  - atp_elo_pre_wimbledon_2024.csv, wta_elo_pre_wimbledon_2024.csv
  - atp_grass_serve_stats.csv, wta_grass_serve_stats.csv
  - wimbledon_2024_atp_features.csv, wimbledon_2024_wta_features.csv
  - wimbledon_2024_features_all_tours.csv  (stacked ATP+WTA features)
"""

from __future__ import annotations

import os
from io import StringIO

import numpy as np
import pandas as pd
import requests

# ─── Paths & season constants ───────────────────────────────────────────────
OUTPUT_DIR = "./tennis_data"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/JeffSackmann"
# Calendar years of CSV files to pull (end exclusive): 2018 … 2024
MATCH_HISTORY_YEARS = list(range(2018, 2025))
FOCUS_YEAR = 2024
PRE_WIMBLEDON_CUTOFF = pd.Timestamp("2024-06-30")

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ─── Download ───────────────────────────────────────────────────────────────
def download_csv(url: str, label: str) -> pd.DataFrame | None:
    print(f"  Downloading {label}...")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        frame = pd.read_csv(StringIO(response.text), low_memory=False)
        print(f"    ✓ {len(frame)} rows")
        return frame
    except Exception as exc:
        print(f"    ✗ Failed: {exc}")
        return None


def clean_matches_dataframe(matches: pd.DataFrame) -> pd.DataFrame:
    """Drop unusable rows and light deduplication; keep column names from source."""
    cleaned = matches.copy()
    before = len(cleaned)

    if "winner_id" in cleaned.columns and "loser_id" in cleaned.columns:
        cleaned = cleaned[cleaned["winner_id"].notna() & cleaned["loser_id"].notna()]
    if "tourney_date" in cleaned.columns:
        cleaned = cleaned[cleaned["tourney_date"].notna()]

    dedupe_keys = [c for c in ("tourney_id", "match_num") if c in cleaned.columns]
    if len(dedupe_keys) == 2:
        cleaned = cleaned.drop_duplicates(subset=dedupe_keys, keep="first")

    for text_col in ("surface", "tourney_name"):
        if text_col in cleaned.columns:
            cleaned[text_col] = cleaned[text_col].astype(str).str.strip()
            cleaned.loc[cleaned[text_col].isin(("nan", "")), text_col] = np.nan

    after = len(cleaned)
    if after < before:
        print(f"    (cleaned {before - after} rows: invalid ids, dates, or dupes)")
    return cleaned.reset_index(drop=True)


def load_matches(tour: str, years: list[int] | None = None) -> tuple[pd.DataFrame, list[int]]:
    """Download yearly CSVs for `tour` ('atp'|'wta'), concat, parse dates, clean.

    Returns ``(matches, failed_years)``.
    """
    years = years or MATCH_HISTORY_YEARS
    chunks: list[pd.DataFrame] = []
    failed_years: list[int] = []

    for year in years:
        url = f"{GITHUB_RAW_BASE}/tennis_{tour}/master/{tour}_matches_{year}.csv"
        chunk = download_csv(url, f"{tour.upper()} {year}")
        if chunk is not None:
            chunk["year"] = year
            chunks.append(chunk)
        else:
            failed_years.append(year)

    if not chunks:
        raise RuntimeError(f"Could not load any {tour.upper()} data!")

    combined = pd.concat(chunks, ignore_index=True)
    combined["tourney_date"] = pd.to_datetime(
        combined["tourney_date"], format="%Y%m%d", errors="coerce"
    )
    combined.sort_values("tourney_date", inplace=True)
    combined.reset_index(drop=True, inplace=True)
    combined = clean_matches_dataframe(combined)
    return combined, failed_years


def combine_atp_wta_matches(atp_matches: pd.DataFrame, wta_matches: pd.DataFrame) -> pd.DataFrame:
    """Single table with a ``tour`` label for joint analysis."""
    atp_labeled = atp_matches.copy()
    wta_labeled = wta_matches.copy()
    atp_labeled["tour"] = "ATP"
    wta_labeled["tour"] = "WTA"
    return pd.concat([atp_labeled, wta_labeled], ignore_index=True)


# ─── Elo ─────────────────────────────────────────────────────────────────────
K_BASE = 32
SURFACE_K_BOOST = 1.2


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))


def compute_elo(
    matches_sorted: pd.DataFrame,
    surface_filter: str | None = None,
    k_factor: float = K_BASE,
) -> dict:
    """Walk matches in order; optional ``surface_filter`` (e.g. 'Grass') for surface Elo."""
    ratings: dict = {}

    for _, row in matches_sorted.iterrows():
        winner_id = row["winner_id"]
        loser_id = row["loser_id"]
        surface = row.get("surface", "") or ""

        rating_w = ratings.get(winner_id, 1500)
        rating_l = ratings.get(loser_id, 1500)
        expected_w = expected_score(rating_w, rating_l)
        expected_l = 1 - expected_w

        if surface_filter is None or surface == surface_filter:
            effective_k = k_factor * SURFACE_K_BOOST if surface_filter else k_factor
            ratings[winner_id] = rating_w + effective_k * (1 - expected_w)
            ratings[loser_id] = rating_l + effective_k * (0 - expected_l)
        else:
            ratings.setdefault(winner_id, 1500)
            ratings.setdefault(loser_id, 1500)

    return ratings


def build_elo_snapshot(matches: pd.DataFrame, cutoff_date: pd.Timestamp) -> pd.DataFrame:
    """Elo and grass Elo using only matches strictly before ``cutoff_date``."""
    history = matches[matches["tourney_date"] < cutoff_date].copy()
    overall = compute_elo(history, surface_filter=None)
    grass = compute_elo(history, surface_filter="Grass")

    player_ids = set(overall.keys()) | set(grass.keys())
    rows = [
        {
            "player_id": pid,
            "overall_elo": round(overall.get(pid, 1500), 2),
            "grass_elo": round(grass.get(pid, 1500), 2),
        }
        for pid in player_ids
    ]
    return pd.DataFrame(rows).sort_values("overall_elo", ascending=False).reset_index(drop=True)


# ─── Features ────────────────────────────────────────────────────────────────
def compute_surface_winpct(
    matches: pd.DataFrame, surface: str, lookback_days: int = 365
) -> dict:
    """player_id -> win rate on ``surface`` in the lookback window before latest date."""
    latest = matches["tourney_date"].max()
    window_start = latest - pd.Timedelta(days=lookback_days)
    on_surface = matches[
        (matches["surface"] == surface) & (matches["tourney_date"] >= window_start)
    ]
    wins = on_surface.groupby("winner_id").size().rename("wins")
    losses = on_surface.groupby("loser_id").size().rename("losses")
    stats = pd.concat([wins, losses], axis=1).fillna(0)
    stats["matches_played"] = stats["wins"] + stats["losses"]
    stats["win_pct"] = stats["wins"] / stats["matches_played"].replace(0, np.nan)
    return stats["win_pct"].to_dict()


def compute_recent_form(matches: pd.DataFrame, last_n_matches: int = 10) -> dict:
    """player_id -> win fraction over last ``last_n_matches`` matches."""
    form: dict = {}
    chronological = matches.sort_values("tourney_date")
    player_ids = set(chronological["winner_id"]) | set(chronological["loser_id"])

    for player_id in player_ids:
        played = chronological[
            (chronological["winner_id"] == player_id) | (chronological["loser_id"] == player_id)
        ].tail(last_n_matches)
        if len(played) == 0:
            form[player_id] = 0.5
        else:
            wins = (played["winner_id"] == player_id).sum()
            form[player_id] = wins / len(played)
    return form


def build_wimbledon_features(
    matches: pd.DataFrame,
    elo_snapshot: pd.DataFrame,
    wimbledon_year: int = FOCUS_YEAR,
) -> pd.DataFrame:
    """Per-match rows for Wimbledon ``wimbledon_year`` with pre-tournament features."""
    wimbledon_matches = matches[
        (matches["tourney_name"].str.contains("Wimbledon", case=False, na=False))
        & (matches["year"] == wimbledon_year)
    ].copy()

    if len(wimbledon_matches) == 0:
        print(f"  ⚠ No Wimbledon {wimbledon_year} matches in data — returning Elo snapshot only")
        return elo_snapshot

    tournament_start = wimbledon_matches["tourney_date"].min()
    prior_matches = matches[matches["tourney_date"] < tournament_start]

    overall_elo_by_player = elo_snapshot.set_index("player_id")["overall_elo"].to_dict()
    grass_elo_by_player = elo_snapshot.set_index("player_id")["grass_elo"].to_dict()
    grass_win_pct_by_player = compute_surface_winpct(prior_matches, "Grass", lookback_days=730)
    recent_form_by_player = compute_recent_form(prior_matches, last_n_matches=10)

    def row_features(row: pd.Series) -> pd.Series:
        winner_id, loser_id = row["winner_id"], row["loser_id"]
        winner_overall = overall_elo_by_player.get(winner_id, 1500)
        loser_overall = overall_elo_by_player.get(loser_id, 1500)
        winner_grass_elo = grass_elo_by_player.get(winner_id, 1500)
        loser_grass_elo = grass_elo_by_player.get(loser_id, 1500)
        return pd.Series(
            {
                "winner_overall_elo": winner_overall,
                "loser_overall_elo": loser_overall,
                "overall_elo_difference": winner_overall - loser_overall,
                "winner_grass_elo": winner_grass_elo,
                "loser_grass_elo": loser_grass_elo,
                "grass_elo_difference": winner_grass_elo - loser_grass_elo,
                "winner_grass_win_pct": grass_win_pct_by_player.get(winner_id, np.nan),
                "loser_grass_win_pct": grass_win_pct_by_player.get(loser_id, np.nan),
                "winner_recent_form": recent_form_by_player.get(winner_id, 0.5),
                "loser_recent_form": recent_form_by_player.get(loser_id, 0.5),
                "higher_overall_elo_won": int(winner_overall > loser_overall),
                "higher_grass_elo_won": int(winner_grass_elo > loser_grass_elo),
            }
        )

    feature_cols = wimbledon_matches.apply(row_features, axis=1)
    return pd.concat([wimbledon_matches.reset_index(drop=True), feature_cols], axis=1)


def compute_grass_serve_stats(matches: pd.DataFrame, lookback_days: int = 365) -> pd.DataFrame:
    """Mean serve metrics on grass over the lookback window (winner+loser rows pooled)."""
    latest = matches["tourney_date"].max()
    window_start = latest - pd.Timedelta(days=lookback_days)
    grass = matches[
        (matches["surface"] == "Grass")
        & (matches["tourney_date"] >= window_start)
        & (matches["w_svpt"].notna())
    ].copy()

    winner_block = grass[
        ["winner_id", "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon"]
    ].copy()
    winner_block.columns = [
        "player_id",
        "aces",
        "double_faults",
        "serve_points",
        "first_serves_in",
        "first_serve_points_won",
        "second_serve_points_won",
    ]
    loser_block = grass[
        ["loser_id", "l_ace", "l_df", "l_svpt", "l_1stIn", "l_1stWon", "l_2ndWon"]
    ].copy()
    loser_block.columns = winner_block.columns

    for block in (winner_block, loser_block):
        block["first_serve_in_pct"] = block["first_serves_in"] / block["serve_points"]
        block["first_serve_won_pct"] = block["first_serve_points_won"] / block[
            "first_serves_in"
        ].replace(0, np.nan)
        second_serves = block["serve_points"] - block["first_serves_in"]
        block["second_serve_won_pct"] = block["second_serve_points_won"] / second_serves.replace(
            0, np.nan
        )
        block["ace_rate"] = block["aces"] / block["serve_points"]

    pooled = pd.concat([winner_block, loser_block], ignore_index=True)
    return (
        pooled.groupby("player_id")[
            ["first_serve_in_pct", "first_serve_won_pct", "second_serve_won_pct", "ace_rate"]
        ]
        .mean()
        .round(4)
        .reset_index()
    )


def attach_serve_stats_to_features(
    wimbledon_features: pd.DataFrame, grass_serve_by_player: pd.DataFrame
) -> pd.DataFrame:
    """Left-join grass serve averages for winner and loser when match-level columns exist."""
    if "winner_id" not in wimbledon_features.columns:
        return wimbledon_features
    serve = grass_serve_by_player.rename(
        columns={
            c: f"winner_grass_{c}"
            for c in grass_serve_by_player.columns
            if c != "player_id"
        }
    )
    out = wimbledon_features.merge(serve, left_on="winner_id", right_on="player_id", how="left")
    out = out.drop(columns=["player_id"], errors="ignore")
    loser_serve = grass_serve_by_player.rename(
        columns={
            c: f"loser_grass_{c}"
            for c in grass_serve_by_player.columns
            if c != "player_id"
        }
    )
    out = out.merge(loser_serve, left_on="loser_id", right_on="player_id", how="left")
    out = out.drop(columns=["player_id"], errors="ignore")
    return out


# ─── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    year = FOCUS_YEAR
    print(f"\n🎾 Tennis {year} pipeline (history {MATCH_HISTORY_YEARS[0]}–{MATCH_HISTORY_YEARS[-1]})\n")
    print("=" * 55)

    print(f"\n📥 Loading ATP matches ({MATCH_HISTORY_YEARS[0]}–{MATCH_HISTORY_YEARS[-1]})...")
    atp_matches, atp_failed_years = load_matches("atp")
    atp_matches.to_csv(f"{OUTPUT_DIR}/atp_matches_all_clean.csv", index=False)
    print(f"  ✓ ATP rows (clean): {len(atp_matches):,}")

    print(f"\n📥 Loading WTA matches ({MATCH_HISTORY_YEARS[0]}–{MATCH_HISTORY_YEARS[-1]})...")
    wta_matches, wta_failed_years = load_matches("wta")
    wta_matches.to_csv(f"{OUTPUT_DIR}/wta_matches_all_clean.csv", index=False)
    print(f"  ✓ WTA rows (clean): {len(wta_matches):,}")

    all_tours = combine_atp_wta_matches(atp_matches, wta_matches)
    all_tours.to_csv(f"{OUTPUT_DIR}/all_tours_matches_clean.csv", index=False)
    print(f"  ✓ Combined ATP+WTA: {len(all_tours):,} rows → all_tours_matches_clean.csv")

    atp_year_slice = atp_matches[atp_matches["year"] == year]
    wta_year_slice = wta_matches[wta_matches["year"] == year]
    atp_year_slice.to_csv(f"{OUTPUT_DIR}/atp_matches_{year}.csv", index=False)
    wta_year_slice.to_csv(f"{OUTPUT_DIR}/wta_matches_{year}.csv", index=False)
    print(f"  ✓ {year} ATP-only: {len(atp_year_slice):,} | WTA-only: {len(wta_year_slice):,}")

    if year in atp_failed_years:
        print(f"  ⚠ ATP: missing upstream file for {year} (empty slice for that year).")
    if year in wta_failed_years:
        print(f"  ⚠ WTA: missing upstream file for {year} (empty slice for that year).")

    print(f"\n📊 Elo snapshots (matches before {PRE_WIMBLEDON_CUTOFF.date()})...")
    atp_elo_snapshot = build_elo_snapshot(atp_matches, PRE_WIMBLEDON_CUTOFF)
    wta_elo_snapshot = build_elo_snapshot(wta_matches, PRE_WIMBLEDON_CUTOFF)
    atp_elo_path = f"{OUTPUT_DIR}/atp_elo_pre_wimbledon_{year}.csv"
    wta_elo_path = f"{OUTPUT_DIR}/wta_elo_pre_wimbledon_{year}.csv"
    atp_elo_snapshot.to_csv(atp_elo_path, index=False)
    wta_elo_snapshot.to_csv(wta_elo_path, index=False)
    print(f"  ✓ ATP players: {len(atp_elo_snapshot):,} | WTA players: {len(wta_elo_snapshot):,}")

    print("\n🎯 Grass serve stats (730-day lookback)...")
    atp_grass_serve = compute_grass_serve_stats(atp_matches, lookback_days=730)
    wta_grass_serve = compute_grass_serve_stats(wta_matches, lookback_days=730)
    atp_grass_serve.to_csv(f"{OUTPUT_DIR}/atp_grass_serve_stats.csv", index=False)
    wta_grass_serve.to_csv(f"{OUTPUT_DIR}/wta_grass_serve_stats.csv", index=False)
    print(f"  ✓ ATP: {len(atp_grass_serve):,} players | WTA: {len(wta_grass_serve):,} players")

    print(f"\n🏆 Wimbledon {year} feature tables...")
    atp_wimbledon = build_wimbledon_features(atp_matches, atp_elo_snapshot, wimbledon_year=year)
    wta_wimbledon = build_wimbledon_features(wta_matches, wta_elo_snapshot, wimbledon_year=year)

    if "winner_id" in atp_wimbledon.columns:
        atp_wimbledon = attach_serve_stats_to_features(atp_wimbledon, atp_grass_serve)
    if "winner_id" in wta_wimbledon.columns:
        wta_wimbledon = attach_serve_stats_to_features(wta_wimbledon, wta_grass_serve)

    atp_feat_path = f"{OUTPUT_DIR}/wimbledon_{year}_atp_features.csv"
    wta_feat_path = f"{OUTPUT_DIR}/wimbledon_{year}_wta_features.csv"
    atp_wimbledon.to_csv(atp_feat_path, index=False)
    wta_wimbledon.to_csv(wta_feat_path, index=False)
    print(f"  ✓ ATP Wimbledon features: {len(atp_wimbledon):,}")
    print(f"  ✓ WTA Wimbledon features: {len(wta_wimbledon):,}")

    if "tourney_name" in atp_wimbledon.columns and "tourney_name" in wta_wimbledon.columns:
        atp_stack = atp_wimbledon.assign(tour="ATP")
        wta_stack = wta_wimbledon.assign(tour="WTA")
        combined_features = pd.concat([atp_stack, wta_stack], ignore_index=True)
        combined_path = f"{OUTPUT_DIR}/wimbledon_{year}_features_all_tours.csv"
        combined_features.to_csv(combined_path, index=False)
        print(f"  ✓ Stacked ATP+WTA features: {len(combined_features):,} → {combined_path}")

    print("\n" + "=" * 55)
    print(f"✅ Saved under {OUTPUT_DIR}/\n")
    print("  atp_matches_all_clean.csv, wta_matches_all_clean.csv")
    print("  all_tours_matches_clean.csv")
    print(f"  atp_matches_{year}.csv, wta_matches_{year}.csv")
    print(f"  atp_elo_pre_wimbledon_{year}.csv, wta_elo_pre_wimbledon_{year}.csv")
    print("  atp_grass_serve_stats.csv, wta_grass_serve_stats.csv")
    print(f"  wimbledon_{year}_atp_features.csv, wimbledon_{year}_wta_features.csv")
    print(f"  wimbledon_{year}_features_all_tours.csv")


if __name__ == "__main__":
    main()
