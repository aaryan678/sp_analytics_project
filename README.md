# sp_analytics_project

Tennis analytics pipeline for **2024**: downloads ATP/WTA match history from Jeff Sackmann’s public CSVs, cleans and merges it, computes **Elo** (overall and grass) through a **pre–Wimbledon 2024** cutoff, and exports **Wimbledon 2024** feature tables for modeling or backtests.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install pandas numpy requests
python data.py
```

Outputs are written to **`./tennis_data/`**. Large downloads are gitignored by default; run `data.py` to regenerate them.

## Data sources and license

- **Match results:** [JeffSackmann/tennis_atp](https://github.com/JeffSackmann/tennis_atp) and [JeffSackmann/tennis_wta](https://github.com/JeffSackmann/tennis_wta) (raw yearly `*_matches_YYYY.csv` files).
- **License:** Sackmann’s data is typically **CC BY-NC-SA 4.0** (non-commercial; attribution required). See the repos for the exact terms.

## Pipeline behavior (what `data.py` does)

| Step | Description |
|------|-------------|
| Download | Pulls ATP and WTA match CSVs for calendar years **2018–2024**. |
| Clean | Drops rows missing `winner_id`, `loser_id`, or `tourney_date`; drops duplicate `(tourney_id, match_num)`; trims text fields. |
| Combine | Builds **`all_tours_matches_clean.csv`** with an extra column **`tour`** = `ATP` or `WTA`. |
| Elo | Uses only matches **strictly before** `2024-06-30` to build pre–Wimbledon Elo snapshots. |
| Features | Filters **Wimbledon 2024** main-draw style rows (`tourney_name` contains “Wimbledon”, `year` = 2024), then attaches pre-tournament features (Elo, form, grass win %, serve aggregates). |
| Stacked features | **`wimbledon_2024_features_all_tours.csv`** stacks ATP and WTA rows and adds **`tour`**. |

---

## Output files

### Match-level files (Sackmann schema + extras)

These rows are **one match per line**. Winner/loser columns follow Sackmann’s naming: **`w_*`** = winner stats, **`l_*`** = loser stats.

| File | Contents |
|------|----------|
| `atp_matches_all_clean.csv` | All downloaded ATP years, cleaned, sorted by date. |
| `wta_matches_all_clean.csv` | Same for WTA. |
| `all_tours_matches_clean.csv` | ATP + WTA; extra column **`tour`** (`ATP` / `WTA`). |
| `atp_matches_2024.csv` | ATP rows where **`year`** = 2024. |
| `wta_matches_2024.csv` | WTA rows where **`year`** = 2024. |

#### Core match and player identity columns

| Header | Meaning |
|--------|---------|
| `tourney_id` | Tournament identifier (string). |
| `tourney_name` | Tournament name (e.g. Wimbledon, Australian Open). |
| `surface` | Court surface (`Hard`, `Clay`, `Grass`, etc.). |
| `draw_size` | Main draw size (integer). |
| `tourney_level` | Event level code (ATP/WTA specific letter codes). |
| `tourney_date` | Start date of the tournament week (parsed as date from `YYYYMMDD`). |
| `match_num` | Match order / id within the event file. |
| `year` | Calendar year of the source CSV (added by this pipeline). |
| `tour` | **Only in** `all_tours_matches_clean.csv`: `ATP` or `WTA`. |

#### Winner columns

| Header | Meaning |
|--------|---------|
| `winner_id` | Numeric player id (Sackmann id). |
| `winner_seed` | Seed, if any. |
| `winner_entry` | Entry status (e.g. WC, Q, LL) when present. |
| `winner_name` | Full name as in the source file. |
| `winner_hand` | R / L (right- or left-handed). |
| `winner_ht` | Height where available. |
| `winner_ioc` | Country / IOC-style code. |
| `winner_age` | Age at match where available. |

#### Loser columns

Same pattern as winner: `loser_id`, `loser_seed`, `loser_entry`, `loser_name`, `loser_hand`, `loser_ht`, `loser_ioc`, `loser_age`.

#### Result and match meta

| Header | Meaning |
|--------|---------|
| `score` | Match score string (source format). |
| `best_of` | Best-of sets (often 3 or 5). |
| `round` | Round code (e.g. R32, QF, F). |
| `minutes` | Match length in minutes when present. |
| `winner_rank` | ATP/WTA singles rank (winner) when present. |
| `winner_rank_points` | Ranking points (winner) when present. |
| `loser_rank` | Rank (loser). |
| `loser_rank_points` | Points (loser). |

#### Serve and return stats (winner `w_*`, loser `l_*`)

| Header | Meaning |
|--------|---------|
| `w_ace` / `l_ace` | Aces. |
| `w_df` / `l_df` | Double faults. |
| `w_svpt` / `l_svpt` | Serve points played. |
| `w_1stIn` / `l_1stIn` | First serves in. |
| `w_1stWon` / `l_1stWon` | Points won on first serve. |
| `w_2ndWon` / `l_2ndWon` | Points won on second serve. |
| `w_SvGms` / `l_SvGms` | Serve games. |
| `w_bpSaved` / `l_bpSaved` | Break points saved. |
| `w_bpFaced` / `l_bpFaced` | Break points faced. |

---

### Elo snapshot files

One row per player who appears in the pre-cutoff history.

| File | Description |
|------|-------------|
| `atp_elo_pre_wimbledon_2024.csv` | ATP Elo through **before** 2024-06-30. |
| `wta_elo_pre_wimbledon_2024.csv` | WTA Elo through the same cutoff. |

| Header | Meaning |
|--------|---------|
| `player_id` | Sackmann player id. |
| `overall_elo` | Elo updated on **all** surfaces (starts 1500). |
| `grass_elo` | Elo updated only on **Grass** matches (same id space; interpret as grass-specific strength). |

---

### Grass serve aggregate files

Per-player **means** over a **730-day** lookback window on **grass** matches that have serve stats (winner and loser rows pooled).

| File | Description |
|------|-------------|
| `atp_grass_serve_stats.csv` | ATP. |
| `wta_grass_serve_stats.csv` | WTA. |

| Header | Meaning |
|--------|---------|
| `player_id` | Player id. |
| `first_serve_in_pct` | `first_serves_in / serve_points` (mean of per-match rates aggregated by player mean in pipeline). |
| `first_serve_won_pct` | Share of first-serve points won when first serve went in. |
| `second_serve_won_pct` | Share of second-serve points won. |
| `ace_rate` | Aces per serve point. |

---

### Wimbledon 2024 feature files

All **original match columns** from the Sackmann row, plus **engineered** columns below. Rows are **Wimbledon 2024** matches only (when present in source data).

| File | Description |
|------|-------------|
| `wimbledon_2024_atp_features.csv` | ATP Wimbledon 2024 + features. |
| `wimbledon_2024_wta_features.csv` | WTA Wimbledon 2024 + features. |
| `wimbledon_2024_features_all_tours.csv` | Vertical stack of ATP + WTA with extra **`tour`** column. |

#### Engineered columns (suffixes describe the side: winner vs loser)

| Header | Meaning |
|--------|---------|
| `winner_overall_elo` | Winner’s **overall** Elo as of pre–Wimbledon cutoff. |
| `loser_overall_elo` | Loser’s overall Elo. |
| `overall_elo_difference` | `winner_overall_elo - loser_overall_elo`. |
| `winner_grass_elo` | Winner’s **grass-only** Elo snapshot. |
| `loser_grass_elo` | Loser’s grass Elo. |
| `grass_elo_difference` | `winner_grass_elo - loser_grass_elo`. |
| `winner_grass_win_pct` | Winner’s win rate on grass in the **730-day** window before tournament start (from prior matches only). |
| `loser_grass_win_pct` | Same for loser. |
| `winner_recent_form` | Winner’s win fraction over their **last 10** matches before Wimbledon (any surface, chronological). |
| `loser_recent_form` | Same for loser. |
| `higher_overall_elo_won` | `1` if winner had higher overall Elo than loser, else `0` (baseline sanity check / label helper). |
| `higher_grass_elo_won` | `1` if winner had higher grass Elo. |
| `winner_grass_first_serve_in_pct` | Grass serve profile joined from aggregates (winner). |
| `winner_grass_first_serve_won_pct` | … |
| `winner_grass_second_serve_won_pct` | … |
| `winner_grass_ace_rate` | … |
| `loser_grass_first_serve_in_pct` | Same four metrics for loser (`loser_grass_*`). |

If Wimbledon rows are missing from upstream data for a year, the pipeline may return only the Elo snapshot table (no match-level columns); in normal 2024 runs you should see full match rows plus the columns above.

---

## Repository layout

```
sp_analytics_project/
  data.py           # Download, clean, Elo, features
  tennis_data/      # Generated CSVs (ignored by git if listed in .gitignore)
  README.md
```

## Contributing / usage note

This repo is intended for **research and learning**. Respect **non-commercial** and **attribution** requirements of the upstream tennis datasets.
