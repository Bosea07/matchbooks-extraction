"""MatchBooks extraction service.
POST /extract (multipart 'file') -> {filename, records[], meta{}}
Contract-compatible with v1; adds meta.confidence, meta.engine, meta.warnings,
multi-format readers and a Claude fallback for low-confidence documents."""
import datetime, os
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from lib.readers import read_any
from lib.parser import parse_grid
from lib import claude_extract

MAX_BYTES = int(os.environ.get('MAX_FILE_MB', '10')) * 1024 * 1024
CONF_THRESHOLD = float(os.environ.get('CONFIDENCE_THRESHOLD', '0.75'))

app = FastAPI(title='matchbooks-extraction', version='2.0.0')
app.add_middleware(CORSMiddleware,
                   allow_origins=os.environ.get('ALLOWED_ORIGINS', '*').split(','),
                   allow_methods=['*'], allow_headers=['*'])

@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'matchbooks-extraction',
            'version': '2.0.0',
            'claudeFallback': claude_extract.available(),
            'time': datetime.datetime.now(datetime.timezone.utc).isoformat()}

@app.post('/extract')
async def extract(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, detail=f'File exceeds {MAX_BYTES // (1024*1024)} MB limit')
    if not data:
        raise HTTPException(400, detail='Empty file')
    filename = file.filename or 'upload'

    # 1) deterministic pipeline
    parse_error = None
    records, meta = [], {}
    try:
        grid, reader_meta = read_any(filename, data)
        records, meta = parse_grid(grid, reader_meta)
        meta['engine'] = 'parser'
    except ValueError as e:
        parse_error = str(e)
        if 'Unsupported file type' in parse_error or 'Legacy .doc' in parse_error:
            raise HTTPException(415, detail=parse_error)
    except Exception as e:  # reader crashed on a malformed file
        parse_error = f'Parser failed: {e}'

    confident = records and meta.get('confidence', 0) >= CONF_THRESHOLD

    # 2) Claude fallback for weak/failed extractions
    if not confident and claude_extract.available():
        try:
            hint = None
            if records:  # give Claude the text we did manage to read
                hint = '\n'.join(
                    f"{r['date']} {r['refRaw']} {r['type']} {r['amount']}" for r in records[:50])
            result = claude_extract.extract(filename, data, text_hint=None)
            if result:
                c_records, c_meta = result
                out_meta = {**{k: meta.get(k, -1) for k in
                               ('headerRow', 'totalRows', 'dateCol', 'refCol', 'typeCol',
                                'debitCol', 'creditCol', 'amountCol')},
                            **c_meta,
                            'parserConfidence': meta.get('confidence', 0.0),
                            'parserWarnings': meta.get('warnings', []),
                            'parserError': parse_error}
                return {'filename': filename, 'records': c_records, 'meta': out_meta}
        except Exception as e:
            meta.setdefault('warnings', []).append(f'Claude fallback failed: {e}')

    # 3) return parser result (or a clear error)
    if not records:
        detail = parse_error or 'No transactions could be extracted from this file'
        if not claude_extract.available():
            detail += ' (AI fallback not configured — set ANTHROPIC_API_KEY to enable)'
        raise HTTPException(422, detail=detail)
    if not confident:
        meta.setdefault('warnings', []).append(
            f"Low confidence ({meta.get('confidence')}) — review the extracted rows before reconciling")
        meta['needsReview'] = True
    return {'filename': filename, 'records': records, 'meta': meta}
