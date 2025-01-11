FROM python:3.9-slim

WORKDIR /app

# Copy only requirements first to leverage Docker cache
COPY requirements.txt .

# Use pip cache and clean up in the same layer to reduce image size
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8008"]
