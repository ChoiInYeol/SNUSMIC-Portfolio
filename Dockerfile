# ============================================================
# Multi-stage Dockerfile for smic-easy
#
# Produces two images depending on the build target:
#   --target pipeline   Python pipeline + collectors + backtest
#   --target web        Next.js static build + runtime
#
# Cross-platform (linux/amd64 + linux/arm64 via buildx):
#   docker buildx build --platform linux/amd64,linux/arm64 --target pipeline .
#   docker buildx build --platform linux/amd64,linux/arm64 --target web .
#
# See docs/DOCKER.md for the full quickstart.
# ============================================================


# ────────────────────────────────────────────────────────────
# Stage 1: Python pipeline base
#   - uv for reproducible installs (uv sync --frozen)
#   - Node 24 + pinned kordoc (tools/kordoc) for PDF→markdown and
#     built-in PP-OCRv5 Korean OCR (transcribe_pdfs.py)
#   - Playwright Chromium for JS-rendered collector pages
# ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS pipeline-base

# Install system deps:
#   - curl + ca-certificates: for uv installer
#   - build-essential, libffi-dev: native extension compilation (e.g. scipy)
#   - fonts + libglib2/libnss3/...: Playwright Chromium runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    build-essential \
    libffi-dev \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy only the lockfile/config first for layer caching
COPY pyproject.toml uv.lock* ./

# Install Python deps (frozen)
RUN uv sync --frozen --no-dev

# Node runtime for kordoc (copied from the official image) + pinned engine
COPY --from=node:24-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
COPY tools/kordoc/package.json tools/kordoc/package-lock.json ./tools/kordoc/
RUN npm ci --prefix tools/kordoc --no-audit --no-fund
COPY tools/kordoc/cpu-limits.mjs ./tools/kordoc/cpu-limits.mjs

# Install Playwright Chromium browser
RUN uv run playwright install chromium

# Copy the rest of the project source
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY src/data/ ./src/data/
COPY public/ ./public/


# ────────────────────────────────────────────────────────────
# Stage 2: pipeline runtime — default entrypoint runs the
#           full daily refresh chain
# ────────────────────────────────────────────────────────────
FROM pipeline-base AS pipeline

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# .env is injected at runtime via -e or --env-file;
# never bake credentials into the image.
# Required vars: KRX_ID, KRX_PW (for collect_kuvic_browser.py)

ENTRYPOINT ["uv", "run", "python"]
CMD ["scripts/backtest_momentum.py"]

# To run the full chain:
#   docker run --rm -v $(pwd)/data:/app/data -v $(pwd)/public:/app/public \
#     --env-file .env pipeline-image \
#     -c "import subprocess, sys; [subprocess.run(['uv','run','python',s], check=True) for s in ['scripts/backtest_momentum.py','scripts/export_signals_api.py']]"
#
# Or use docker-compose (see docker-compose.yml) — preferred.


# ────────────────────────────────────────────────────────────
# Stage 3: Node.js build stage
# ────────────────────────────────────────────────────────────
FROM node:20-slim AS node-build

WORKDIR /app

# Copy package files first for layer caching
COPY package.json package-lock.json* ./

# Install all deps (including devDeps needed for build)
RUN npm ci

# Copy the rest of the Next.js source
COPY next.config.ts tsconfig.json postcss.config.* tailwind.config.* ./
COPY src/ ./src/
COPY public/ ./public/

# Build the Next.js static site
RUN npm run build


# ────────────────────────────────────────────────────────────
# Stage 4: Web runtime — serves the built Next.js app
# ────────────────────────────────────────────────────────────
FROM node:20-slim AS web

ENV NODE_ENV=production
ENV PORT=3000

WORKDIR /app

# Only copy what next start needs
COPY --from=node-build /app/.next/standalone ./
COPY --from=node-build /app/.next/static ./.next/static
COPY --from=node-build /app/public ./public

EXPOSE 3000

CMD ["node", "server.js"]

# Note: The "standalone" output requires next.config.ts to have:
#   output: "standalone"
# If not set, use a static file server instead:
#   COPY --from=node-build /app/out ./out
#   RUN npm install -g serve
#   CMD ["serve", "out", "-l", "3000"]
