FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        dvisvgm \
        ffmpeg \
        ghostscript \
        libcairo2-dev \
        libffi-dev \
        libgdk-pixbuf-2.0-0 \
        libglib2.0-0 \
        libgl1 \
        libpango1.0-dev \
        pkg-config \
        shared-mime-info \
        texlive-fonts-recommended \
        texlive-latex-base \
        texlive-latex-extra \
        texlive-science \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
