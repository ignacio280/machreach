# Running MachReach on one small server

The cheapest way to run the product without changing it: Postgres, the web
service, the always-on worker, and Caddy for HTTPS, all on one small VPS.
Same code, same gunicorn command, same five-second job pickup as Render, on
roughly a quarter of the bill. Everything in `deploy/` is automated; the
steps a person must take are listed under *Your part*.

Recommended server: Hetzner Cloud CPX11 in Ashburn (2 vCPU, 2 GB RAM, 40 GB
disk, about 5 USD a month including the IPv4 address) on Ubuntu 24.04.
Ashburn is the closest Hetzner location to Chile. Any other provider works
as long as the image is Ubuntu 24.04 and it accepts cloud-init user data.

## What the stack does

- `Dockerfile` builds the app exactly as Render did: Python 3.13, the hashed
  `requirements.lock`, gunicorn with one preloaded worker and eight threads.
- `deploy/docker-compose.yml` runs `db` (Postgres 17 on a named volume),
  `web`, `worker` (`python worker.py`, the long-running scheduler), and
  `caddy` (ports 80 and 443, automatic Let's Encrypt certificates, plus a
  plain-HTTP listener on 8080 for checking the server before DNS moves).
- `deploy/bootstrap.sh` runs once from cloud-init: firewall, timers, image
  build, and, when `RENDER_DATABASE_URL` is set, a full copy of the Render
  database with row-count verification (`scripts/copy_database.py`).
- `deploy/deploy.sh` runs every two minutes from a systemd timer. When the
  tracked branch has a new commit whose GitHub check runs have all passed
  (`deploy/checks_passed.py`, the equivalent of Render's `checksPass`
  trigger), it rebuilds the image, runs `migrate.py`, and swaps the
  containers. Red or still-running CI is never deployed.
- `deploy/backup.sh` runs nightly at 04:30 UTC and keeps fourteen days of
  `pg_dump` files in `/var/backups/machreach`.
- `machreach-cutover` (installed by the bootstrap) stops the app, wipes the
  local database, copies Render again, verifies, and starts the app.

## Your part

1. **Create the server.** Hetzner Cloud, new project, *Add server*: Ashburn,
   Ubuntu 24.04, CPX11, add your SSH key. Open `deploy/cloud-init.yaml` from
   this repository, fill in the values in its `content:` block (copy each
   secret from the Render web service's environment page; `POSTGRES_PASSWORD`
   is any new long random string; `RENDER_DATABASE_URL` is the database's
   **external** connection string from its Render page), and paste the whole
   file into the *Cloud config* box. Create the server and note its IP.
2. **Wait about five minutes**, then open `http://<ip>:8080/health` in a
   browser. `{"status":"healthy"}` means the stack is up and holds a copy of
   the production data as of a few minutes ago. `ssh root@<ip> tail
   /var/log/machreach-bootstrap.log` shows what happened if it does not.
3. **Cut over**, in a quiet window (03:00 to 04:00 Santiago):
   - In Render, suspend the `machreach` web service and the
     `machreach-worker` service so nothing writes to the old database.
   - `ssh root@<ip> machreach-cutover` copies the final state of the Render
     database and prints the row-count table; it must end with `verified`.
   - At your DNS provider, point the `machreach.com` A record at the server's
     IP (and remove any Render CNAME). Caddy obtains the certificate within a
     minute of DNS propagating.
   - Open `https://machreach.com/health`, log in, open a course. Ask for a
     quiz; it starts within seconds.
4. **After seven days**, delete the three Render resources, and remove
   `RENDER_DATABASE_URL` from `/opt/machreach/deploy/.env`.

That is the whole list. Nothing else needs a login of yours.

## Rollback

Until the Render resources are deleted, rollback is: resume the two Render
services and point the DNS A record back at Render. Writes made on the server
after the cutover stay on the server; `deploy/backup.sh` output or a
`pg_dump` of the local database can be restored to Render with
`scripts/copy_database.py --force` if they must be kept.

## Operating it

- **Logs**: `cd /opt/machreach/deploy && docker compose logs -f web worker`.
- **Deploy by hand**: `machreach-deploy` (waits for green checks) or
  `machreach-deploy --force` (deploys the checked-out commit as is).
- **Follow master** after this branch is merged: `cd /opt/machreach && git
  checkout master && machreach-deploy --force`.
- **Backups**: `machreach-backup` writes one now. Copy the newest file off the
  server now and then; Hetzner's server snapshots (about 20% of the server
  price) are the one-click alternative.
- **Health**: the `uptime.yml` GitHub workflow keeps probing
  `https://machreach.com/health` and `/health/operations`; its
  `UPTIME_ORIGIN_HEALTH_URL` default still points at Render's
  `onrender.com` hostname, which only produces a notice, not a failure.
- **Shell scripts** from `scripts/` run inside the web container:
  `docker compose run --rm --no-deps web python scripts/grant_admin.py ...`.
- **Ubuntu updates** install themselves (`unattended-upgrades`); reboot when
  `/var/run/reboot-required` appears. Containers restart on boot.

## What is different from Render

- Certificates, the firewall, OS updates, and backups are the server's job,
  automated above, but nobody else is watching them. Check the backup
  directory and `docker compose ps` once a month.
- One box: a disk failure loses the database up to the last copied backup.
  The nightly dump plus an occasional off-server copy is what meets the
  documented 24-hour RPO.
- No Render dashboard: `docker compose ps`, the two log commands above, and
  Sentry are the views into production.
