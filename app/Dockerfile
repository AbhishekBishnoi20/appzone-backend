FROM python:3.9-slim

WORKDIR /app

# Copy only requirements first to leverage Docker cache
COPY requirements.txt .

# Use pip cache and clean up in the same layer to reduce image size
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Create an empty __init__.py file to make the directory a Python package
RUN touch __init__.py

COPY . .

ENV PYTHONPATH=/app
