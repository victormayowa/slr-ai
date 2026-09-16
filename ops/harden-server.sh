#!/usr/bin/env bash
# One-time hardening of a fresh Ubuntu 22.04/24.04 server for OmniReview. Run as root (sudo), after adding your SSH key
# for the deploy user and confirming you can sign in with it.
#
#   sudo DEPLOY_USER=deploy ops/harden-server.sh
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:?Set DEPLOY_USER to the account you deploy with}"
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo" >&2; exit 1; }
if [ ! -s "/home/$DEPLOY_USER/.ssh/authorized_keys" ]; then
  echo "$DEPLOY_USER has no SSH key; add one first or you will be locked out" >&2
  exit 1
fi

apt-get update
apt-get install -y ufw fail2ban unattended-upgrades apt-listchanges curl restic postgresql-client

# Firewall: SSH, HTTP (for certificate issuance and redirects), and HTTPS only. Postgres and Redis stay private.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# SSH: keys only, no root login.
install -d /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/10-omnireview.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
CONF
systemctl reload ssh || systemctl reload sshd

# Ban repeated failed SSH logins.
cat > /etc/fail2ban/jail.d/omnireview.local <<'CONF'
[sshd]
enabled = true
maxretry = 5
bantime = 1h
CONF
systemctl enable --now fail2ban

# Security updates install themselves.
dpkg-reconfigure -f noninteractive unattended-upgrades

# Docker keeps container logs bounded.
install -d /etc/docker
cat > /etc/docker/daemon.json <<'CONF'
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
CONF
systemctl restart docker 2>/dev/null || true

install -d -m 700 -o "$DEPLOY_USER" /var/backups/omnireview
echo "Server hardened. Next: install the systemd timers in ops/systemd (see docs/production-readiness.md)."
