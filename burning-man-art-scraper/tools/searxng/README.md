# Local SearXNG for Enrichment

This is optional. The main scraper does not require Docker or SearXNG.

Start the local service:

```powershell
cd tools\searxng
docker compose up -d
```

Confirm the JSON endpoint works:

```powershell
Invoke-RestMethod "http://localhost:8080/search?q=Burning+Man&format=json&language=en&safesearch=1"
```

Use it for enrichment:

```powershell
$env:SEARXNG_BASE_URL="http://localhost:8080"
$env:ENRICHMENT_SEARCH_PROVIDER="searxng"
python run_scraper.py
```

Stop the service:

```powershell
cd tools\searxng
docker compose down
```

The compose file binds SearXNG to `127.0.0.1:8080` so it is not exposed publicly by default.
