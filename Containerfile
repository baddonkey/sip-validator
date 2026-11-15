FROM docker.io/library/ubuntu:26.04

ARG KOST_TOOLS_VERSION=1.3.1.0
ARG KOST_TOOLS_SHA256=bb8e7c197b8a4da8442f5b8395c3df44213c171aa1863e656322f41a49c910ae

ENV DEBIAN_FRONTEND=noninteractive
# Ubuntu 26.04's Wine 10 completes prefix initialisation and provides newer Windows
# networking APIs for the bundled Java 21 runtime. Upgrade to WineHQ 11 after the
# prefix and KOST-Tools are installed; WineHQ 11 handles KOST-Val's native helpers.
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl wine wine64 wine32:i386 xvfb xauth python3 python3-venv \
 && ln -s /usr/lib/x86_64-linux-gnu/wine/wineserver /usr/local/bin/wineserver \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/sip-validator-api
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home kost && mkdir /data && chown kost:kost /data
USER kost
ENV WINEPREFIX=/home/kost/.wine \
    WINEARCH=win64 \
    WINEDEBUG=-all

COPY --chown=kost:kost scripts/install-kost-tools.sh /tmp/install-kost-tools.sh
RUN bash /tmp/install-kost-tools.sh

USER root
RUN mkdir -p /etc/apt/keyrings \
 && curl -fsSL -o /etc/apt/keyrings/winehq-archive.asc https://dl.winehq.org/wine-builds/winehq.key \
 && curl -fsSL -o /etc/apt/sources.list.d/winehq-resolute.sources https://dl.winehq.org/wine-builds/ubuntu/dists/resolute/winehq-resolute.sources \
 && sed -i 's/winehq-archive.key/winehq-archive.asc/' /etc/apt/sources.list.d/winehq-resolute.sources \
 && apt-get update \
 && apt-get install -y --install-recommends winehq-stable \
 && ln -sf /opt/wine-stable/bin/wine /usr/local/bin/wine \
 && ln -sf /opt/wine-stable/bin/wineserver /usr/local/bin/wineserver \
 && rm -rf /var/lib/apt/lists/*
USER kost

COPY --chown=kost:kost app ./app

ENV PATH=/opt/venv/bin:$PATH \
    SIP_VALIDATOR_DATA_DIR=/data \
    KOSTVAL_LAUNCHER="xvfb-run -a"

VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
