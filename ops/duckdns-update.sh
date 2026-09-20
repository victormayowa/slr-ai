#!/usr/bin/env bash
# Keep a DuckDNS domain pointing at this server. Run by omnireview-duckdns.timer every five minutes.
#
# Settings live in /etc/omnireview/duckdns.env (readable only by root):
#   DUCKDNS_DOMAIN=omnireview      # the name only, without .duckdns.org
#   DUCKDNS_TOKEN=...              # from https://www.duckdns.org
#
# DuckDNS takes the address the request comes from, so a server with one public address needs no IP here. Set
# DUCKDNS_IP to say which address to publish when the server has several.
set -euo pipefail

ENV_FILE="${DUCKDNS_ENV_FILE:-/etc/omnireview/duckdns.env}"
if [ -r "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090  # the path is an operator setting, not a file in the repository
  . "$ENV_FILE"
  set +a
fi

DOMAIN="${DUCKDNS_DOMAIN:?Set DUCKDNS_DOMAIN, for example omnireview}"
TOKEN="${DUCKDNS_TOKEN:?Set DUCKDNS_TOKEN from your DuckDNS account}"

response="$(curl -fsS --max-time 20 --retry 3 --retry-delay 5 \
  "https://www.duckdns.org/update?domains=${DOMAIN}&token=${TOKEN}&ip=${DUCKDNS_IP:-}")"

# DuckDNS answers "OK" or "KO"; it never says why, so a failure means the domain or token is wrong.
if [ "$response" != "OK" ]; then
  echo "DuckDNS refused the update for ${DOMAIN}.duckdns.org (check DUCKDNS_DOMAIN and DUCKDNS_TOKEN)" >&2
  exit 1
fi
echo "${DOMAIN}.duckdns.org now points at this server"
