# syntax=docker/dockerfile:1
# `service` is a named Bake context built with the service's own Dockerfile.
# Flatten only the assembly inputs into an OCI artifact. In particular, do not
# transport the service's base OS, compiler build tree or chown-copyup layers.
# hadolint global ignore=DL3022
FROM scratch AS app
COPY --from=service --chown=0:0 /usr/local/app /usr/local/app

FROM scratch AS ledger
COPY --from=service --chown=0:0 /usr/local/bin/ledger /usr/local/bin/ledger

FROM scratch AS liaison
COPY --from=service --chown=0:0 /usr/local/bin/liaison /usr/local/bin/liaison

FROM scratch AS vault
COPY --from=service --chown=0:0 /usr/local/bin/secrets-server /usr/local/bin/secrets-server

FROM scratch AS lode
COPY --from=service --chown=0:0 /usr/local/bin/lode /usr/local/bin/lode
COPY --from=service --chown=0:0 /opt/elan /opt/elan
COPY --from=service --chown=0:0 /opt/lode/cache /opt/lode/cache

FROM scratch AS lun
COPY --from=service --chown=0:0 /usr/local/bin/lun /usr/local/bin/lun
COPY --from=service --chown=0:0 /opt/elan /opt/elan
COPY --from=service --chown=0:0 /opt/lun/cache /opt/lun/cache
