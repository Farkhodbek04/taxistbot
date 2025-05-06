# Base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY taxi_bot.py .
COPY config.json .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "taxi_bot.py"]