from datetime import datetime, timezone

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from lib.parser import extract_from_bytes

app = FastAPI(title="matchbooks-extraction")

# Allow the Lovable frontend (or anything else) to call this service directly
# from the browser. Locked down to specific origins later if needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "matchbooks-extraction",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    content = await file.read()

    try:
        result = extract_from_bytes(file.filename or "", content)
    except ValueError as err:
        return JSONResponse(status_code=400, content={"error": "extraction failed", "detail": str(err)})
    except Exception as err:  # noqa: BLE001 - surface unexpected parser errors to the caller
        return JSONResponse(status_code=500, content={"error": "unexpected extraction error", "detail": str(err)})

    return {
        "filename": file.filename,
        "records": result["records"],
        "meta": result["meta"],
    }
