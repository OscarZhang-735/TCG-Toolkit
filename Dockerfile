FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG BUILD_SHA=local
ENV TCG_BUILD_SHA=$BUILD_SHA
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[web]' && useradd --create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "tcg_toolkit.web:app", "--host", "0.0.0.0", "--port", "8000"]
