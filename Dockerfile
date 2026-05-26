# syntax=docker/dockerfile:1.7
#
# Cloud Cost Anomaly Agent — Lambda container image
#
# All dependencies are pure Python (py3-none-any wheels) so the same
# build steps produce a correct image on linux/amd64 AND linux/arm64
# with no cross-compilation or QEMU needed.
#
# Supported platforms:
#   linux/amd64  — x86_64 Lambda, Ubuntu CI, Intel Mac
#   linux/arm64  — arm64 Lambda (Graviton), Apple Silicon Mac
#
# Build (native platform, for local testing):
#   docker build -t cost-anomaly-agent .
#
# Build multi-platform and push to ECR:
#   make ecr-push
#
# Run locally (reads .env.local for credentials):
#   make run-local

ARG PYTHON_VERSION=3.12
ARG FUNCTION_DIR="/var/task"

# BUILDPLATFORM and TARGETPLATFORM are BuildKit automatic build args.
# Declaring them before the first FROM makes them available in FROM lines.
ARG BUILDPLATFORM
ARG TARGETPLATFORM

# ---------------------------------------------------------------------------
# Stage 1 — deps
# Run pip on the BUILD platform so it's always fast (no emulation for pip).
# Because all wheels are py3-none-any, the installed files are identical
# on every target platform.
# ---------------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM python:${PYTHON_VERSION}-slim AS deps

ARG FUNCTION_DIR
# Re-declare so the value is visible inside this stage
ARG BUILDPLATFORM
ARG TARGETPLATFORM

WORKDIR /install

# Print platform info — useful during the demo to show cross-platform build working
RUN echo "Building deps: BUILDPLATFORM=${BUILDPLATFORM} → TARGETPLATFORM=${TARGETPLATFORM}"

COPY requirements.txt .

RUN pip install \
        --no-cache-dir \
        --no-compile \
        --target ${FUNCTION_DIR} \
        -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2 — final Lambda image (uses TARGETPLATFORM automatically)
# public.ecr.aws/lambda/python:3.12 publishes a multi-arch manifest
# covering linux/amd64 and linux/arm64.
# ---------------------------------------------------------------------------
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION}

ARG FUNCTION_DIR

# Copy installed packages from the deps stage
COPY --from=deps ${FUNCTION_DIR} ${LAMBDA_TASK_ROOT}

# Copy agent source — exclude tests and dev files (see .dockerignore)
COPY agent.py          ${LAMBDA_TASK_ROOT}/
COPY tools/            ${LAMBDA_TASK_ROOT}/tools/

# Lambda invokes this function for every event
CMD ["agent.lambda_handler"]
