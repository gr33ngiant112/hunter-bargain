FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy project files needed for build/install
COPY pyproject.toml README.md ./

# Copy application source
COPY src/ src/

# Install the package (includes all dependencies)
RUN pip install --no-cache-dir .

# Create data directory for SQLite
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "hunter_bargain.main:app", "--host", "0.0.0.0", "--port", "8000"]
