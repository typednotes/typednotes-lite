# syntax=docker/dockerfile:1
# `combined` is the Lite Bake target: both variants use identical service builds.
# hadolint ignore=DL3006
FROM combined
# Prevent package installation from starting a disposable build-time server.
# hadolint ignore=DL3008
RUN groupadd --system --gid 10007 postgres \
    && useradd --system --uid 10007 --gid 10007 --create-home --home-dir /var/lib/postgresql postgres \
    && printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-17 postgresql-contrib-17 \
    && rm -rf /var/lib/apt/lists/* \
    && pg_dropcluster --stop 17 main \
    && rm /usr/sbin/policy-rc.d
COPY local/*.py /usr/local/lib/typednotes/local/
ENV TYPEDNOTES_DATA_DIR=/data
VOLUME /data
HEALTHCHECK --interval=30s --timeout=15s --start-period=180s --retries=3 \
  CMD ["python3", "/usr/local/lib/typednotes/local/entrypoint.py", "--healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/usr/local/lib/typednotes/local/entrypoint.py"]
