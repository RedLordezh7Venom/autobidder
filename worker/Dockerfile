# Use a base image with Python and system dependencies for Playwright
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Set work directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The base image already has browsers, but we ensure chromium is ready
RUN playwright install chromium

# Copy the rest of the code
COPY . .

# Ensure directories for screenshots and sessions exist
RUN mkdir -p screenshots sessions

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Expose the port Render will provide
EXPOSE 8080

# Start the application using uvicorn
# We use the PORT env var provided by Render
CMD uvicorn main:app --host 0.0.0.0 --port $PORT
