# syntax=docker/dockerfile:1
# Named BuildKit contexts are external source-build targets, not local stages.
# hadolint global ignore=DL3022
# The six named contexts are source builds supplied by docker-bake.hcl.
# Trixie satisfies the app's glibc requirement and runs the older service
# binaries too. Lean and the native dependencies are still needed at runtime.
FROM docker.io/library/debian:trixie-slim
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ARG LODE_LINEN_REF
ARG LUN_LINEN_REF
ARG LODE_TOOLCHAIN
ADD https://raw.githubusercontent.com/typednotes/linen/${LODE_LINEN_REF}/ci/native-deps/apt.txt /tmp/lode-apt.txt
ADD https://raw.githubusercontent.com/typednotes/linen/${LUN_LINEN_REF}/ci/native-deps/apt.txt /tmp/lun-apt.txt
# Versions and intentional word splitting come from Linen's canonical apt list.
# hadolint ignore=DL3008,SC2046
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 tini bash tar gzip postgresql-client $(sed 's/#.*//' /tmp/lode-apt.txt /tmp/lun-apt.txt | sort -u) \
    && rm -rf /var/lib/apt/lists/* \
    && uid=10001 \
    && for service in app ledger liaison vault lode lun; do \
         useradd --system --uid "$uid" --create-home --home-dir "/var/lib/$service" "$service" \
           && chmod 700 "/var/lib/$service" || exit 1; \
         uid=$((uid + 1)); \
       done
COPY --from=app /usr/local/app /usr/local/app
COPY --from=ledger /usr/local/bin/ledger /usr/local/bin/ledger
COPY --from=liaison /usr/local/bin/liaison /usr/local/bin/liaison
COPY --from=vault /usr/local/bin/secrets-server /usr/local/bin/secrets-server
COPY --from=lun /usr/local/bin/lun /usr/local/bin/lun
COPY --from=lode /usr/local/bin/lode /usr/local/bin/lode
# Install the union in ONE layer: identical toolchains occupy disk once, while
# differing release toolchains remain available to each worker's private home.
RUN --mount=from=lode,source=/opt/elan,target=/mnt/lode \
    --mount=from=lun,source=/opt/elan,target=/mnt/lun \
    mkdir -p /opt/elan/toolchains /opt/typednotes \
    && cp -a /mnt/lun/bin /opt/elan/ \
    && cp -a /mnt/lode/toolchains/. /opt/elan/toolchains/ \
    && cp -a /mnt/lun/toolchains/. /opt/elan/toolchains/ \
    && chown -R root:root /opt/elan \
    && chmod -R a+rX /opt/elan \
    && cp /mnt/lode/settings.toml /opt/typednotes/lode-settings.toml \
    && cp /mnt/lun/settings.toml /opt/typednotes/lun-settings.toml \
    && chmod 644 /opt/typednotes/lode-settings.toml /opt/typednotes/lun-settings.toml
COPY --from=lode --chown=0:0 /opt/lode/cache /opt/lode/cache
COPY --from=lun --chown=0:0 /opt/lun/cache /opt/lun/cache
COPY lite/runtime.py /usr/local/lib/typednotes-lite/runtime.py
COPY lite/runtime.py /usr/local/lib/typednotes/lite/runtime.py
COPY images/*.py /usr/local/lib/typednotes/images/
COPY .images/manifest.json /usr/local/share/typednotes/manifest.json
COPY .images/migrations /usr/local/share/typednotes/migrations
ENV PATH=/opt/elan/bin:${PATH} \
    PYTHONPATH=/usr/local/lib/typednotes \
    LODE_LINEN_REF=${LODE_LINEN_REF} \
    LODE_TOOLCHAIN=${LODE_TOOLCHAIN} \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_DIR=/etc/ssl/certs \
    PORT=8080
WORKDIR /usr/local/app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=15s --start-period=180s --retries=3 \
  CMD ["python3", "/usr/local/lib/typednotes-lite/runtime.py", "--healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/usr/local/lib/typednotes-lite/runtime.py"]
