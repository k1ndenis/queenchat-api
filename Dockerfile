FROM python:3.14-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --target /app/deps

FROM python:3.14-slim AS production

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/deps ./deps

ENV PYTHONPATH=/app/deps
ENV PATH="/app/deps/bin:${PATH}"

COPY . .

EXPOSE 3001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3002"]