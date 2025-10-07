ARG PYTHON_VERSION=3.13
ARG DISTRO_NAME=alpine

# FROM python:3.13-alpine
FROM python:$PYTHON_VERSION-$DISTRO_NAME

ENV PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on

RUN apk --no-cache add ffmpeg

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt --no-cache-dir

CMD ["python", "bot/main.py"]