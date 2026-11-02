FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 appuser
COPY examples ./examples
COPY scripts ./scripts
USER appuser
CMD ["python", "scripts/demo.py"]
