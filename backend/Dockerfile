FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    gcc \
    g++ \
    wget \
    make \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Build TA-Lib C library from source
RUN wget -q https://github.com/TA-Lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz \
    && tar -xzf ta-lib-0.6.4-src.tar.gz \
    && cd ta-lib-0.6.4 \
    && ./configure --prefix=/usr \
    && make -j$(nproc) \
    && make install \
    && cd .. \
    && rm -rf ta-lib-0.6.4 ta-lib-0.6.4-src.tar.gz

ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV CHROMIUM_PATH=/usr/bin/chromium

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install-deps chromium

COPY . .

CMD ["python", "server.py"]
