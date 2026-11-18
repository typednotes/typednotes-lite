// scripts/build-images.sh resolves the newest stable service tags into peeled
// commits in .images/bake.json. There is deliberately no main/old-tag fallback.
variable "APP_REF" { type = string }
variable "LEDGER_REF" { type = string }
variable "LIAISON_REF" { type = string }
variable "SECRETS_REF" { type = string }
variable "LODE_REF" { type = string }
variable "LUN_REF" { type = string }
variable "LITE_IMAGE" { default = "ghcr.io/typednotes/typednotes-lite:latest" }
variable "LOCAL_IMAGE" { default = "ghcr.io/typednotes/typednotes-local:latest" }
variable "PLATFORM" { default = "linux/amd64" }

group "default" { targets = ["lite", "local"] }

target "_platform" { platforms = [PLATFORM] }

target "app" {
  inherits = ["_platform"]
  context = "https://github.com/typednotes/typednotes.git#${APP_REF}"
  cache-from = ["type=gha,scope=app-${replace(PLATFORM, "/", "-")}"]
  cache-to = ["type=gha,scope=app-${replace(PLATFORM, "/", "-")},mode=max"]
}
target "ledger" {
  inherits = ["_platform"]
  context = "https://github.com/typednotes/ledger.git#${LEDGER_REF}"
  cache-from = ["type=gha,scope=ledger-${replace(PLATFORM, "/", "-")}"]
  cache-to = ["type=gha,scope=ledger-${replace(PLATFORM, "/", "-")},mode=max"]
}
target "liaison" {
  inherits = ["_platform"]
  context = "https://github.com/typednotes/liaison.git#${LIAISON_REF}"
  cache-from = ["type=gha,scope=liaison-${replace(PLATFORM, "/", "-")}"]
  cache-to = ["type=gha,scope=liaison-${replace(PLATFORM, "/", "-")},mode=max"]
}
target "vault" {
  inherits = ["_platform"]
  context = "https://github.com/typednotes/secrets.git#${SECRETS_REF}"
  cache-from = ["type=gha,scope=vault-${replace(PLATFORM, "/", "-")}"]
  cache-to = ["type=gha,scope=vault-${replace(PLATFORM, "/", "-")},mode=max"]
}
target "lode" {
  inherits = ["_platform"]
  context = "https://github.com/typednotes/lode.git#${LODE_REF}"
  cache-from = ["type=gha,scope=lode-${replace(PLATFORM, "/", "-")}"]
  cache-to = ["type=gha,scope=lode-${replace(PLATFORM, "/", "-")},mode=max"]
}
target "lun" {
  inherits = ["_platform"]
  context = "https://github.com/typednotes/lun.git#${LUN_REF}"
  cache-from = ["type=gha,scope=lun-${replace(PLATFORM, "/", "-")}"]
  cache-to = ["type=gha,scope=lun-${replace(PLATFORM, "/", "-")},mode=max"]
}

// CI selects ONE artifact target on each fresh service runner. The final
// assembly runners consume these flattened images by digest, not build trees.
target "app-artifact" {
  inherits = ["_platform"]
  context = "."
  dockerfile = "images/artifact.Dockerfile"
  target = "app"
  contexts = { service = "target:app" }
}
target "ledger-artifact" {
  inherits = ["_platform"]
  context = "."
  dockerfile = "images/artifact.Dockerfile"
  target = "ledger"
  contexts = { service = "target:ledger" }
}
target "liaison-artifact" {
  inherits = ["_platform"]
  context = "."
  dockerfile = "images/artifact.Dockerfile"
  target = "liaison"
  contexts = { service = "target:liaison" }
}
target "vault-artifact" {
  inherits = ["_platform"]
  context = "."
  dockerfile = "images/artifact.Dockerfile"
  target = "vault"
  contexts = { service = "target:vault" }
}
target "lode-artifact" {
  inherits = ["_platform"]
  context = "."
  dockerfile = "images/artifact.Dockerfile"
  target = "lode"
  contexts = { service = "target:lode" }
}
target "lun-artifact" {
  inherits = ["_platform"]
  context = "."
  dockerfile = "images/artifact.Dockerfile"
  target = "lun"
  contexts = { service = "target:lun" }
}

target "lite" {
  inherits = ["_platform"]
  context = "."
  dockerfile = "lite/Dockerfile"
  contexts = {
    app = "target:app"
    ledger = "target:ledger"
    liaison = "target:liaison"
    vault = "target:vault"
    lode = "target:lode"
    lun = "target:lun"
  }
  tags = [LITE_IMAGE]
  cache-from = ["type=gha,scope=lite-${replace(PLATFORM, "/", "-")}"]
  cache-to = ["type=gha,scope=lite-${replace(PLATFORM, "/", "-")},mode=max"]
  labels = {
    "org.opencontainers.image.source" = "https://github.com/typednotes/typednotes-infra"
    "org.opencontainers.image.description" = "Typednotes Lite: app, ledger, liaison, vault, Lode and Lun"
  }
}

target "local" {
  inherits = ["_platform"]
  context = "."
  dockerfile = "local/Dockerfile"
  contexts = { combined = "target:lite" }
  tags = [LOCAL_IMAGE]
  cache-from = ["type=gha,scope=local-${replace(PLATFORM, "/", "-")}"]
  cache-to = ["type=gha,scope=local-${replace(PLATFORM, "/", "-")},mode=max"]
  labels = {
    "org.opencontainers.image.source" = "https://github.com/typednotes/typednotes-infra"
    "org.opencontainers.image.description" = "Typednotes Local: six services and persistent PostgreSQL"
  }
}
