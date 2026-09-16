# syntax=docker/dockerfile:1
# Adhan & Bell Pager — SIP paging for the Adhan and school bells.
#
# Stage 1 builds PJSIP with the pjsua2 Python bindings. Stage 2 is a slim runtime
# that contains only the PJSIP shared libraries, the bindings, ffmpeg and the app.

ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim-bookworm AS pjsip
ARG PJ_VERSION=2.14.1
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config swig wget ca-certificates \
        libasound2-dev libssl-dev libopus-dev uuid-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN wget -q "https://github.com/pjsip/pjproject/archive/refs/tags/${PJ_VERSION}.tar.gz" -O pj.tar.gz \
    && tar xzf pj.tar.gz && mv "pjproject-${PJ_VERSION}" pjproject && rm pj.tar.gz
WORKDIR /build/pjproject
RUN printf 'export CFLAGS += -fPIC -O2\nexport CXXFLAGS += -fPIC -O2\n' > user.mak \
    && ./configure --prefix=/opt/pjsip --enable-shared --disable-video --disable-libyuv --disable-libwebrtc \
    && make dep && make -j"$(nproc)" && make install
WORKDIR /build/pjproject/pjsip-apps/src/swig/python
RUN make \
    && mkdir -p /opt/pjsua2-py \
    && cp pjsua2.py /opt/pjsua2-py/ \
    && find build -name "_pjsua2*.so" -exec cp {} /opt/pjsua2-py/ \; \
    && ls -l /opt/pjsua2-py

FROM python:${PYTHON_VERSION}-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LD_LIBRARY_PATH=/opt/pjsip/lib \
    CONFIG_PATH=/config/config.yaml \
    AUDIO_DIR=/audio \
    WEB_PORT=8080
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg tzdata ca-certificates libasound2 libssl3 libopus0 libuuid1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app
COPY --from=pjsip /opt/pjsip/lib /opt/pjsip/lib
COPY --from=pjsip /opt/pjsua2-py/ /usr/local/lib/python3.11/site-packages/
RUN python -c "import pjsua2; ep = pjsua2.Endpoint(); ep.libCreate(); print('pjsua2', ep.libVersion().full); ep.libDestroy()"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import geonamescache as g; assert len(g.GeonamesCache().get_cities()) > 1000"
COPY app ./app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh && python -m compileall -q app

VOLUME ["/config", "/audio"]
EXPOSE 8080 5060/udp 5060/tcp
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('WEB_PORT','8080'), timeout=4)" || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "app"]
