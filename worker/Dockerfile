# Use a lightweight Python base image
FROM python:3.11-slim-bookworm

# Set the working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Expose the port (Render provides this via the PORT env var)
EXPOSE 8080

# Start Uvicorn
CMD uvicorn main:app --host 0.0.0.0 --port $PORT
