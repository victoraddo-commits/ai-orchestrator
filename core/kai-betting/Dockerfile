FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core ./core

ENV KAI_BETTING_DB=/data/kai_betting.db
CMD ["python", "-m", "core.kai_betting.run_worker"]
