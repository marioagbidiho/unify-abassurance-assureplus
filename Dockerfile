# Image de l'API de prédiction (P8) - dépendances verrouillées, roues binaires uniquement,
# utilisateur non root
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 unify
WORKDIR /app
COPY requirements/api.lock ./requirements/api.lock
RUN pip install --no-cache-dir --only-binary :all: --require-hashes -r requirements/api.lock
COPY src ./src
COPY config ./config
COPY data/demo ./data/demo
ENV PYTHONPATH=/app/src UNIFY_ARTIFACTS=/app/data/demo PYTHONUNBUFFERED=1
USER unify
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
# Dans le conteneur, l'écoute sur toutes les interfaces est nécessaire (réseau Docker isolé)
CMD ["uvicorn", "unify.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
