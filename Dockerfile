FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .

# Install PyTorch CPU-only separately (requires special index URL)
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install the rest of the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory for SQLite persistence
RUN mkdir -p /app/data

EXPOSE 8000

# Default command: run the web dashboard server
CMD ["python", "-m", "app.main", "web", "--host", "0.0.0.0", "--port", "8000"]
