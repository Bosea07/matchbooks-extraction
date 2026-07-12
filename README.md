# matchbooks-extraction

Python/FastAPI microservice for MatchBooks — deployed on Railway as its own service.

Dedicated to reading vendor/Zoho files in any format (Excel/CSV/PDF) and returning normalized transaction JSON to `matchbooks-api`. See the plan doc, Section 5, for why this is Python rather than Node, and for the layered format-recognition design (known-template match → generic heuristic detection → confidence scoring → Claude API fallback).

## Local dev
```
pip install -r requirements.txt
uvicorn main:app --reload      # http://localhost:8000/health
```

## Deploy
Railway → same project as `matchbooks-api` → Add a new service → Deploy from GitHub repo → select `matchbooks-extraction`. Railway reads the `Procfile` to know how to start it.
