# Load test (NFR-01, NFR-03)

`scripts/load_test.py` fills a conference with users and papers and lets 50 virtual users (1 chair, reviewers and authors) use it at the same time for 30 seconds. It measures every request and checks NFR-01: **95% of normal requests within 1 second with up to 50 users**. With `--full` the conference has the size of NFR-03: **1000 submissions and 300 reviewers** (PDFs up to 20 MB are covered by the upload tests, the load test uses small PDFs).

## How to run it

Start the system (`docker compose up --build`), then:

```bash
pip install requests pypdf
python scripts/load_test.py --full --base-url http://localhost:5002          # straight at the CMT service (needs its port published)
python scripts/load_test.py --full --base-url http://localhost:5001 --prefix /api/cmt   # through the orchestrator
python scripts/load_test.py --full --base-url https://localhost --prefix /api/cmt --insecure   # through HTTPS proxy and interface
```

Use a test system: it creates about 400 accounts per run. Every run uses new addresses. The script exits with 1 when NFR-01 is not met or more than 1% of the requests fail.

## First measurement (24 Nov 2026) — not the final one

Setup: everything on one MacBook (Apple M1 Pro), CMT service started with `python app.py` (the same Flask development server the Dockerfile uses), **SQLite** database instead of PostgreSQL, password hashing at the real cost of 12, load script on the same machine. 1000 papers, 300 reviewers, 50 users, 30 seconds, directly at the CMT service.

| | Before the fix | After the fix |
|---|---|---|
| Requests | 9329 | 9335 |
| Requests per second | 311 | 311 |
| Errors | 0 | 0 |
| p50 / p95 / p99, all requests | 0.031 / 0.166 / 0.389 s | 0.029 / 0.187 / 0.472 s |
| **NFR-01 (p95 under 1 s)** | met | met |
| Chair's results overview (`/conferences/<id>/results`), median | **7.2 s** | 0.05 s |

The load test found one real problem: the results overview of the chair (T11) asked the database several times per paper and took more than 7 seconds with 1000 papers. It rarely shows in the overall numbers because the chair calls it seldom, but a chair waiting 7 seconds is not a "normal request within 1 second". It now uses three queries for the whole conference. `tests/test_decisions.py` has a test that fails if the number of queries grows with the number of papers again.

## What this does NOT show yet
- It was measured against SQLite on a development server on one laptop, not PostgreSQL in Docker with four containers. **Repeat it at home with Docker** and put the numbers here.
- It does not include the AI assistant (NFR-02, 95% within 10 seconds): that is not built yet.
- Registering and logging in were not part of the 50-user phase. A login costs about a quarter of a second of processor time on purpose (bcrypt cost 12, SEC-02).
- The Flask development server is not meant for production. For real use a production server such as gunicorn is needed. We did not switch, because the service creates its tables when it starts, which would collide when several workers start at the same time.
- A browser and mobile check (NFR-04) needs a web interface, which does not exist yet.
