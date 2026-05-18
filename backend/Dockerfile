FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
RUN chmod +x scripts/entrypoint.sh
CMD ["scripts/entrypoint.sh"]
