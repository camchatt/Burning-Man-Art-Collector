# Burning Man Art Collector

Aggregator that turns Burning Man art listings into Artelier-ready CSVs, with a local hub for preview, year processing, and deploy packaging.

**Start here:** [`START_HERE.md`](START_HERE.md)

## Quick start (most users)

```bash
cd burning-man-art-scraper
py run_aggregator_hub.py
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/)

In Cursor/VS Code, the hub can also start automatically when you open this folder (allow automatic tasks if prompted).

## What you do in the hub

1. **Process another year** — enter the year, choose a `PlayaEvents-YYYY_ART.csv`, click **Run ingest (offline)**.
2. **Preview** — use filters such as **Needs attention**; open a card and check display name vs source credit, hero image, proof link, and playa address.
3. **Validate CSV** — confirms the file matches Artelier’s 36-column import schema.
4. **Prepare deploy package** — writes `burning-man-art-scraper/data/deploy/{year}/artelier_core_only_{year}.csv`.
5. Upload that core CSV in the Artelier admin import UI.

Optional: set `admin_import_url` in [`burning-man-art-scraper/config/artelier_deploy.yaml`](burning-man-art-scraper/config/artelier_deploy.yaml) so **Open Artelier import** jumps to your import page.

## Source list format

Upload files are PlayaEvents **ART** CSVs (same family as files under `What When Where Files/`), with columns such as:

`Title`, `Description`, `Type`, `Camp`, `Where`, `Extra`, `Link`, `UID`

## Disk notes

- Hero images stay as **remote URLs** (not downloaded).
- Year outputs **overwrite** under `data/bm_ingest/{year}/` — no growing snapshot piles.
- Cleanup: `py run_aggregator_hub.py --cleanup`

## Deeper docs

| Doc | Audience |
|-----|----------|
| [`START_HERE.md`](START_HERE.md) | Fast orientation |
| [`burning-man-art-scraper/viewer/aggregator/README.md`](burning-man-art-scraper/viewer/aggregator/README.md) | Hub UI walkthrough |
| [`burning-man-art-scraper/README.md`](burning-man-art-scraper/README.md) | Full scraper / CLI / verification |
| [`burning-man-art-scraper/docs/burning_man_ingestion_rules.md`](burning-man-art-scraper/docs/burning_man_ingestion_rules.md) | Field rules and review flags |
