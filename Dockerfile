FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy project definition and install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application source
COPY src/ src/

# Install the package in editable mode (source is already copied)
RUN pip install --no-cache-dir -e .

# Create data directory for SQLite
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "hunter_bargain.main:app", "--host", "0.0.0.0", "--port", "8000"]
