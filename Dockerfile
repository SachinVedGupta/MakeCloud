# Docker file for running FastAPI app
FROM python:3.12.3-slim

WORKDIR /model
COPY . /model

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

CMD ["uvicorn", "model:app", "--host", "0.0.0.0", "--port", "8080"]