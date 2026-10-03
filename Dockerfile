# Trusted single-operator backend. Deploy the Next.js frontend separately.
FROM hashicorp/terraform:1.9.8 AS terraform
FROM python:3.12-slim
WORKDIR /app
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && useradd --create-home makecloud
COPY app.py model.py run_cloud.py ./
RUN mkdir /data && chown makecloud:makecloud /data
USER makecloud
ENV MAKECLOUD_DATA_DIR=/data
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
