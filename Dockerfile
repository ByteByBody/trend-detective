FROM python:3.15-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Pinned to the environment that generated data/cache/analysis: the cache
# filename hashes NumPy's version and whether statsmodels is importable, so a
# different build would silently miss the warm bodies and recompute for minutes.
COPY requirements-deploy.txt ./
RUN pip install -r requirements-deploy.txt

COPY . .
RUN pip install -e . --no-deps

# The warm cache is a public GitHub release asset, not part of the repository:
# the build pulls the tarball that matches this code's analysis signature.
RUN python -m deploy.fetch_cache

EXPOSE 7860

CMD ["uvicorn", "trend_detective.api.app:app", "--host", "0.0.0.0", "--port", "7860"]
