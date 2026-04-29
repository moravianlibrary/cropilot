FROM trinera/smart-crop-base:1.1.0 AS base

USER root
# Copy source code
COPY app /src/app/
COPY models /src/models/

# create volume dirs at build-time if corresponding env vars are set
RUN /bin/sh -c '\
    if [ -n "$MODELS_VOLUME_PATH" ]; then \
        mkdir -p "$MODELS_VOLUME_PATH" && chown -R appuser:appuser "$MODELS_VOLUME_PATH" || true; \
    fi; \
    if [ -n "$SCANS_VOLUME_PATH" ]; then \
        mkdir -p "$SCANS_VOLUME_PATH" && chown -R appuser:appuser "$SCANS_VOLUME_PATH" || true; \
    fi'

ENV MODELS_VOLUME_PATH="${MODELS_VOLUME_PATH}"
ENV SCANS_VOLUME_PATH="${SCANS_VOLUME_PATH}"

USER appuser
