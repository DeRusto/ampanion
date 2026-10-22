# ampanion

Your Plexamp companion for playlists that make themselves.

ampanion runs next to your Plex server. Sign in with Plex, pick which Plex users are Listeners, and turn on Mixes for each one. Every night ampanion refreshes their Playlists directly in their own Plex account. Tautulli is optional.

## Install

With Docker Compose:

```sh
mkdir ampanion && cd ampanion
curl -O https://raw.githubusercontent.com/DeRusto/ampanion/main/docker-compose.yml
docker compose up -d
```

Without Compose:

```sh
docker run -d --name ampanion \
  -e TZ=Etc/UTC -e PUID=1000 -e PGID=1000 \
  -v ./ampanion:/config \
  -p 8484:8484 \
  --restart unless-stopped \
  ghcr.io/derusto/ampanion:latest
```

Then open `http://<your-server>:8484`.

- `TZ`: your timezone (for example `Europe/Berlin`), so the nightly run and "day" follow your local time.
- `PUID` / `PGID`: the host user and group that own the files in `/config` (default 1000/1000).
- `/config` holds all state. To back up or move ampanion, copy that one folder.

Images are on GHCR for `linux/amd64` and `linux/arm64`, tagged `X.Y.Z`, `X.Y` and `latest`.

## HTTPS

ampanion serves plain HTTP on port 8484. To reach it from outside your LAN, put it behind a reverse proxy that handles HTTPS (for example Caddy, Traefik, Nginx Proxy Manager or SWAG), and don't expose port 8484 to the internet directly.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
