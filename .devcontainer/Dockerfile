FROM python:3.11.5-slim AS builder

WORKDIR /opt/pysetup

COPY requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --target /opt/pysetup/deps -r requirements.txt

FROM python:3.11.5-slim

WORKDIR /app

COPY --from=builder /opt/pysetup/deps /usr/local/lib/python3.11/site-packages

# Keep the image self-contained for fresh deployments. In local compose usage,
# the bind mount still overrides /app with the live workspace.
COPY . .

ENV PATH="/usr/local/lib/python3.11/site-packages/bin:${PATH}"

EXPOSE 80
