FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

ENV MQTT_HOST=localhost
ENV MQTT_PORT=1883
ENV MQTT_BASE_TOPIC=iot/nodes
ENV DEVICE_TOKEN=changeme
ENV API_PORT=8000
ENV STATE_FILE=/app/data/runtime_state.json

EXPOSE 8000

CMD ["python", "agent.py"]