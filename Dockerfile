# Hosted single-instance backend; Terraform runs in separate constrained containers.
FROM hashicorp/terraform:1.9.8 AS terraform
FROM docker:28-cli AS dockercli
FROM python:3.12-slim
WORKDIR /app
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && useradd --create-home makecloud
COPY app.py model.py run_cloud.py assistant.py blueprints.py domain.py store.py cloud_connections.py account_mail.py hcp_runner.py hcp_connections.py ./
RUN mkdir /data && chown makecloud:makecloud /data
USER makecloud
ENV MAKECLOUD_DATA_DIR=/data
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
