# Backend API (lv/api.py) for Railway, Render or any Docker host.
# The price library is built from the dataset in data/raw at build time (P1, P2, P3: 720 priced items).
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY lv lv
COPY vlm vlm
COPY ["data/raw/Challenge A Dataset", "/app/raw"]
RUN LV_DATA=/app/seed python -m lv ingest "/app/raw/[ORT_024] LV24069.D86" --project P1 \
 && LV_DATA=/app/seed python -m lv ingest "/app/raw/StBALV23040.D86" --project P2 \
 && LV_DATA=/app/seed python -m lv ingest "/app/raw/Angebot AN24-0305.D84" --project P3 \
      --texts "/app/raw/Angebot AN24-0305.pdf" "/app/raw/HEM2301-2_Gesamt-LV.pdf"
COPY deploy/start.sh /app/start.sh
EXPOSE 8000
CMD ["sh", "/app/start.sh"]
