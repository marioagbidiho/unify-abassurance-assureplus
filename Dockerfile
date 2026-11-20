# Image de l'API de prédiction (P8) - multi-stage, utilisateur non root
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir --prefix=/install ".[api]"

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 unify
WORKDIR /app
COPY --from=build /install /usr/local
COPY config ./config
COPY data/demo ./data/demo
ENV UNIFY_ARTIFACTS=/app/data/demo PYTHONUNBUFFERED=1
USER unify
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "unify.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
