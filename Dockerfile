# Stage 1: The builder image (highly optimized)
FROM python:3.13 AS builder

# Create the app directory and set working directory
RUN mkdir /app
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

# Install poetry
RUN pip install poetry==1.8.5

# Copy project files and install dependencies
COPY arxiv-api ../arxiv-api
COPY arxiv-base ../arxiv-base
COPY arxiv-browse ../arxiv-browse
COPY latextranslate ../latextranslate
COPY pyproject.toml poetry.lock ./
RUN  poetry lock --no-update && poetry install && rm -rf $POETRY_CACHE_DIR


# Stage 2: Production stage
# FROM python:3.13-slim-bullseye AS runtime # no libssl.so.3
FROM python:3.13-slim-bookworm AS runtime

# Install system packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl libxml2 nginx supervisor texlive-latex-extra texlive-xetex && \
    rm -rf /var/lib/apt/lists/*

# Install tectonic
RUN curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net |sh && mv tectonic /usr/local/bin/

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/.venv/lib/python3.13/site-packages:$PYTHONPATH"

# Copy project files
COPY cenxiv ./cenxiv
COPY articles ./articles
COPY locale ./locale
COPY config ./config
COPY manage.py ./manage.py

# Create cache dir for latextranslate
RUN chmod 777 /root && mkdir /root/.latextranslate && chmod 777 /root/.latextranslate

# Copy Nginx config
COPY config/nginx/default.conf.template /etc/nginx/conf.d/default.conf

# Copy supervisord config
COPY config/supervisor/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose port for Nginx
EXPOSE 8080

# Start supervisor, uwsgi and nginx
CMD ["/usr/bin/supervisord", "-n"]