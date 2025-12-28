FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml

RUN pip install --no-cache-dir uv && uv pip install --system "fastapi>=0.115.0" "httpx>=0.27.0" "uvicorn[standard]>=0.30.0"

COPY main.py /app/main.py

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]


