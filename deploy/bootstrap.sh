#!/usr/bin/env bash
# First boot of a fresh Ubuntu server, run once by cloud-init after it has
# installed Docker, cloned the repository to /opt/machreach, and written
# deploy/.env. Everything below is idempotent, so it is also safe to re-run
# by hand: `bash /opt/machreach/deploy/bootstrap.sh`.
set -euo pipefail

REPO_DIR=${MACHREACH_DIR:-/opt/machreach}
cd "$REPO_DIR"
log() { echo "[bootstrap] $*"; }

[[ -f deploy/.env ]] || { log "deploy/.env is missing; copy deploy/env.example and fill it in"; exit 1; }
chmod 600 deploy/.env
chmod +x deploy/*.sh

# Swap: the cheapest servers have 1 GB of memory, and Postgres, the app, the
# worker, and Caddy together sit close to that. Two gigabytes of swap turn a
# tight moment into a slow one instead of an out-of-memory kill.
if [[ ! -f /swapfile ]]; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl -q vm.swappiness=10
    echo 'vm.swappiness=10' > /etc/sysctl.d/90-machreach.conf
fi

# Firewall: SSH, HTTP, HTTPS, and the pre-cutover check port.
if command -v ufw >/dev/null; then
    ufw allow 22/tcp >/dev/null
    ufw allow 80/tcp >/dev/null
    ufw allow 443/tcp >/dev/null
    ufw allow 443/udp >/dev/null
    ufw allow 8080/tcp >/dev/null
    ufw --force enable >/dev/null
fi

# Operator commands.
ln -sf "$REPO_DIR/deploy/deploy.sh" /usr/local/bin/machreach-deploy
ln -sf "$REPO_DIR/deploy/backup.sh" /usr/local/bin/machreach-backup
install -m 755 /dev/stdin /usr/local/bin/machreach-cutover <<CUT
#!/usr/bin/env bash
exec "$REPO_DIR/deploy/copy_from_render.sh" --replace
CUT

# Timers: auto-deploy every two minutes, backup nightly.
cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now machreach-deploy.timer machreach-backup.timer

# Build and start. With RENDER_DATABASE_URL set, copy the data first.
export GIT_COMMIT
GIT_COMMIT=$(git rev-parse HEAD)
(cd deploy && docker compose build --pull web && docker compose up -d db)
if grep -qE '^RENDER_DATABASE_URL=.+' deploy/.env; then
    log "copying the Render database"
    deploy/copy_from_render.sh
fi
deploy/deploy.sh --force

ip=$(curl -fsS -4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
log "up. Check http://$ip:8080/health, then point the domain's A record at $ip."
