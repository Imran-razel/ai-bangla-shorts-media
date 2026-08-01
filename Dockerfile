FROM python:3.11-slim

# Install ffmpeg + certs + curl
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        curl \
        fontconfig && \
    rm -rf /var/lib/apt/lists/*

# Bangla font (Noto Sans Bengali) - direct download, apt package name unreliable across base images
RUN mkdir -p /usr/share/fonts/truetype/noto-bengali && \
    curl -sL -o /usr/share/fonts/truetype/noto-bengali/NotoSansBengali-Regular.ttf \
        "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansBengali/NotoSansBengali-Regular.ttf" && \
    fc-cache -f

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Render provides $PORT at runtime
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
