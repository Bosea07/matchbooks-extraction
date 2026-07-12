from fastapi import FastAPI
from datetime import datetime, timezone

app = FastAPI(title="matchbooks-extraction")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "matchbooks-extraction",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/extract")
def extract():
    # Placeholder. Stage 2 fills this in with the layered format-recognition
    # pipeline: file-type detection -> known-template match -> generic heuristic
    # column detection -> confidence scoring -> Claude API fallback.
    return {
        "error": "not implemented yet",
        "note": "Stage 2 will add the universal SOA format recognition pipeline here.",
    }
