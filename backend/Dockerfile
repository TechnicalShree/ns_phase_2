FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 HF_HOME=/app/.hf DATA_DIR=/app/data
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# bake the embedding model into the image — no download at runtime
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY . .

EXPOSE 8080
# seed the sandbox on first boot, then serve
CMD sh -c '[ -f "$DATA_DIR/suppliers.db" ] || python generate_sandbox.py; \
    exec uvicorn app.main:app --host 0.0.0.0 --port 8080'
