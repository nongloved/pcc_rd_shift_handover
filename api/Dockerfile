FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api.py ./

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
