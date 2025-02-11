# Stage 1: The builder image, used to build the virtual environment
FROM python:3.13 AS builder

# Create the app directory
RUN mkdir /app

# Set the working directory
WORKDIR /app

# Install poetry
RUN pip install poetry==1.8.5

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

# Install dependencies
COPY arxiv-api ../arxiv-api
COPY arxiv-base ../arxiv-base
COPY arxiv-browse ../arxiv-browse
COPY latextranslate ../latextranslate
RUN touch README.md
COPY pyproject.toml poetry.lock ./
RUN  poetry lock --no-update && poetry install && rm -rf $POETRY_CACHE_DIR


# Stage 2: Production stage
FROM python:3.13

# Add a non-root user
RUN if ! getent group cenxivuser; then groupadd -r cenxivuser; fi && if ! getent passwd cenxivuser; then useradd -m -r -g cenxivuser cenxivuser; fi

# Create the app directory
RUN mkdir /app && chown cenxivuser:cenxivuser /app

# Swithch to this user
USER cenxivuser

# Set the working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Copy the Python dependencies from the builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy the Django project
COPY cenxiv ./cenxiv
COPY articles ./articles
COPY locale ./locale
COPY config ./config
COPY manage.py ./manage.py
# COPY .env ./.env

# Expose the Django port
EXPOSE 8000

# Run Django’s development server
# CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
CMD ["uwsgi", "--ini", "/app/config/uwsgi/uwsgi.ini"]
