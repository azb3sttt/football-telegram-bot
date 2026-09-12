FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .
COPY . .
CMD ["python", "-m", "app"]
