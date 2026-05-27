FROM trinera/smart-crop-base:1.2.0 AS base

USER root
# Copy source code
COPY app /src/app/
COPY models /src/models/

USER appuser
