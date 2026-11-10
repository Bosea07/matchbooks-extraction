"""Service-level checks for v2.22: the extraction cache, Server-Timing, and /reconcile still answering.

Plain script with a __main__ runner like the other suites. Needs fastapi + httpx (TestClient)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("EXTRACT_CACHE_SIZE", "2")

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

client = TestClient(main.app)
CSV = b"Date,Reference,Type,Amount\n01-10-2026,INV-1001,Invoice,1200.00\n05-10-2026,INV-1002,Invoice,850.50\n09-10-2026,PAY-77,Payment,-1200.00\n"
calls = {"n": 0}
_real = main._extract_uncached


def _counting(*a, **k):
    calls["n"] += 1
    return _real(*a, **k)


main._extract_uncached = _counting


def post(name, data, engine="parser"):
    return client.post("/extract", files={"file": (name, data, "text/csv")}, data={"engine": engine})


def test_first_read_parses_and_times():
    main._cache.clear(); calls["n"] = 0
    r = post("a.csv", CSV)
    assert r.status_code == 200, r.text
    assert len(r.json()["records"]) == 3
    assert r.headers.get("server-timing", "").startswith("app;dur="), r.headers
    assert calls["n"] == 1


def test_same_bytes_come_from_cache_with_new_name():
    main._cache.clear(); calls["n"] = 0
    first = post("a.csv", CSV).json()
    again = post("renamed.csv", CSV).json()
    assert calls["n"] == 1, "second upload of identical bytes must not be read again"
    assert again["filename"] == "renamed.csv"
    assert again["meta"].get("cached") is True and not first["meta"].get("cached")
    assert again["records"] == first["records"]


def test_cache_copy_is_isolated():
    main._cache.clear()
    r1 = post("a.csv", CSV).json()
    r1["records"].clear()                      # a caller mutating its copy must not touch the cache
    assert len(post("a.csv", CSV).json()["records"]) == 3


def test_engine_is_part_of_the_key():
    main._cache.clear(); calls["n"] = 0
    post("a.csv", CSV, "parser")
    post("a.csv", CSV, "auto")
    assert calls["n"] == 2


def test_lru_limit():
    main._cache.clear()
    for i in range(4):
        post(f"f{i}.csv", CSV + f"10-10-2026,INV-{2000 + i},Invoice,1.00\n".encode())
    assert len(main._cache) == 2


def test_failures_are_not_cached():
    main._cache.clear()
    r = post("bad.csv", b"nothing useful here\n")
    assert r.status_code in (415, 422), r.status_code
    assert len(main._cache) == 0


def test_reconcile_still_answers():
    body = {"vendorTransactions": [{"ref": "INV-1", "amount": 100, "dateISO": "2026-10-01"}],
            "zohoTransactions": [{"ref": "INV-1", "amount": 100, "dateISO": "2026-10-01"}]}
    r = client.post("/reconcile", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["matched"] == 1
    assert "server-timing" in r.headers


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f"  ok   {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"  FAIL {t.__name__}: {e!r}")
    print(f"{len(tests) - failed} passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
