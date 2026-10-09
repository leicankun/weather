FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements-media.txt ./
RUN pip install --no-cache-dir -r requirements.txt
ARG INSTALL_MEDIA=false
RUN if [ "$INSTALL_MEDIA" = "true" ]; then apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 && pip install --no-cache-dir -r requirements-media.txt && rm -rf /var/lib/apt/lists/*; fi
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
