FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY yolo_tracker ./yolo_tracker
COPY class_mapping.yolo26m-multiscale-v1.json ./class_mapping.yolo26m-multiscale-v1.json
RUN mkdir -p /app/data
EXPOSE 8200
CMD ["uvicorn", "yolo_tracker.app:app", "--host", "0.0.0.0", "--port", "8200"]
