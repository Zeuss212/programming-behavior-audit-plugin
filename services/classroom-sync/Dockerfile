# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

WORKDIR /app/services/classroom-sync

COPY services/classroom-sync /app/services/classroom-sync
COPY contracts /app/contracts
COPY myextension/api_schemas /app/plugin-schemas

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install /app/services/classroom-sync

ENV PYTHONUNBUFFERED=1 \
    CLASSROOM_CONTRACTS_ROOT=/app/contracts/classroom/v1 \
    CLASSROOM_PLUGIN_SCHEMA_ROOT=/app/plugin-schemas

EXPOSE 8080
