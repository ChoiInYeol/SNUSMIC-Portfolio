# Docker — Build & Run Guide

Two images are defined in the multi-stage `Dockerfile`:

| Image | Target | Purpose |
|---|---|---|
| `smic-pipeline` | `pipeline` | Python pipeline: collect → transcribe → backtest → export API |
| `smic-web` | `web` | Next.js production server (Vercel handles this in CI) |

---

## Prerequisites

- Docker 24+ (with BuildKit enabled by default)
- Docker Compose v2 (`docker compose`, not `docker-compose`)
- `.env` file at project root (see below)

---

## Environment variables

Create a `.env` file (never commit it):

```bash
# .env — copy from .env.example and fill in
KRX_ID=your_krx_id
KRX_PW=your_krx_password
```

The pipeline uses these for `collect_kuvic_browser.py` (KRX login).
If you are only running `backtest_momentum.py` + `export_signals_api.py`
(no new PDF collection), these can be left empty.

---

## Quickstart (one command)

```bash
# 1. Run the full daily pipeline (backtest + export API files)
docker compose --profile pipeline run --rm pipeline

# 2. Inspect generated signals
cat public/api/v1/signals/latest.json | python -m json.tool | head -40
```

---

## Build images

```bash
# Build both images
docker compose build

# Build only the pipeline image
docker compose build pipeline

# Build only the web image
docker compose build web

# Cross-platform (Mac M-series + linux/amd64) — requires buildx
docker buildx build --platform linux/amd64,linux/arm64 --target pipeline -t smic-pipeline:latest .
docker buildx build --platform linux/amd64,linux/arm64 --target web -t smic-web:latest .
```

---

## Run just the pipeline

```bash
# Full chain: backtest_momentum.py → export_signals_api.py
docker compose --profile pipeline run --rm pipeline

# Export only (if strategy-backtest.json already exists)
docker compose --profile pipeline run --rm pipeline scripts/export_signals_api.py

# Single script with args
docker compose --profile pipeline run --rm pipeline scripts/backtest_momentum.py
```

Volumes are bind-mounted:

| Host path | Container path | Purpose |
|---|---|---|
| `./data` | `/app/data` | Price cache, PDFs, markdown (persisted across runs) |
| `./public` | `/app/public` | API JSON output written here |
| `./src/data` | `/app/src/data` | `strategy-backtest.json` output |

---

## Run the web server locally

```bash
# Start the Next.js production server on port 3000
docker compose --profile web up web

# Open http://localhost:3000
```

> **Note:** On Vercel, the `web` image is not used — Vercel builds and serves
> Next.js directly from the repository.  Use `web` only for local testing or
> self-hosted deployments.

---

## Run pipeline + web together

```bash
docker compose --profile pipeline --profile web up
```

The `public/` directory is bind-mounted into the web container (read-only),
so updated API files are visible without rebuilding the image.

---

## Platform notes

### Mac (Apple Silicon / M-series)

`linux/arm64` is the native platform. The Dockerfile uses the official
`python:3.12-slim` and `node:20-slim` base images, both of which publish
multi-arch manifests, so builds work natively without emulation.

```bash
# Native arm64 build (fastest on M-series)
docker build --target pipeline -t smic-pipeline:latest .
```

### Linux (amd64 / CI)

Standard `linux/amd64`. GitHub Actions uses `ubuntu-latest` which is amd64.
The CI workflow already runs `backtest_momentum.py` directly with `uv`; Docker
is provided for local reproducibility and self-hosted use.

### Windows

Docker Desktop on Windows runs containers inside a Linux VM (WSL2 or Hyper-V),
so all containers run `linux/amd64`.

> **Transcription engine:** `transcribe_pdfs.py` runs kordoc (Node, pinned in
> `tools/kordoc`) for PDF→markdown and its built-in PP-OCRv5 Korean OCR. The
> `pipeline` image copies Node 24 from `node:24-slim` and runs
> `npm ci --prefix tools/kordoc`; no JDK or Windows-only OCR is involved.

---

## Dockerfile stage overview

```
python:3.12-slim
  └── pipeline-base   (uv + JDK 21 + Playwright + Python deps)
        └── pipeline  (final pipeline image; entrypoint: uv run python)

node:20-slim
  └── node-build      (npm ci + next build)
        └── web       (production runtime; node server.js)
```

---

## Docker build check (no Docker installed)

If Docker is not installed on this machine, verify syntax with:

```bash
docker build --check .       # requires Docker 24.0.6+ with BuildKit
# or
hadolint Dockerfile          # static linter
```

The Dockerfile was authored according to Docker best practices:
- Multi-stage to minimise final image size
- Dependencies installed before source copy (layer cache hits)
- No secrets baked in (`.env` is in `.dockerignore`)
- `--no-install-recommends` on all `apt-get install` calls
- Non-root user can be added trivially if required by your security policy
