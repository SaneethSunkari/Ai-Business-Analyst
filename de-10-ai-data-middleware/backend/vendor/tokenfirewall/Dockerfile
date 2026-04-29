FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY tokenfirewall ./tokenfirewall
RUN pip install --no-cache-dir -e .

ENTRYPOINT ["python", "-m", "tokenfirewall"]
