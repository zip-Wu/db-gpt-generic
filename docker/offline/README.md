# DB-GPT Offline Deployment

Target architecture: Linux x86-64 (linux/amd64). Python is managed with UV.
Two images: `dbgpt-generic` (application) and `dbgpt-sandbox-python`
(analysis sandbox). Build both with `build-frontend.sh` + `build-backend.sh`
in this directory, or load pre-built archives with `docker load`.

## Install

1. Verify the delivery archive using its SHA256 file, then extract it.
2. Run `docker load -i images.tar.gz` (Docker supports gzip archives).
3. Copy `site.env.example` to `site.env`, set SITE_APP_KEY and SITE_APP_SECRET
   (if your model gateway requires per-request signing), then run
   `chmod 600 site.env`. Never put credentials in the images.
4. If your LLM gateway uses a private CA, put the CA chain in
   `certs/customer-ca.pem` and set `SITE_CA_FILE=/app/certs/customer-ca.pem`
   in site.env. The certificate must match the gateway hostname/IP. For a
   controlled test only, `verify_tls` in site.toml can be set to false
   explicitly.
5. Run `bash start.sh`. Default URL: http://127.0.0.1:5670.
   For LAN access use `DBGPT_BIND_ADDRESS=<server-IP> bash start.sh`.
6. Add your business database in the UI (Oracle / MySQL / ...). Use the
   site's database credentials.

Docker Engine must already be installed. Compose is not required. The supplied
start script uses the host Docker socket to create sibling analysis containers;
the application therefore needs a trusted host and trusted administrators.
The socket is never mounted into the analysis sandbox.

## Services and Storage

LLM and Embedding endpoints are configured in `site.toml` / `site.env`
(any OpenAI-compatible endpoint works; a per-request SM3-signed gateway is
available as provider `proxy/customer_gateway`). Both must be reachable from
the application container. The context limit defaults to 32768; adjust after
your model's real limit is known. Authentication timestamps require the host
clock to be correct.

The application uses SQLite metadata and local Chroma storage in data/pilot.
Business databases are external data sources, not the platform metadata
database. data/logs contains logs. Back up both directories and the local
configuration. Keep the deployment directory fixed: sandbox bind mounts use
its absolute path. Uploaded inputs are mounted read-only, each execution
working directory read-write. Sandbox containers have no network, no
application secrets or Docker socket, a read-only root filesystem, 1 GiB
memory, 2 CPU and 128 process limits. They are removed after execution,
including timeout failures.

Python, shell, file analysis and the subagent Python/shell tools use the
Docker backend (enable with `DBGPT_SANDBOX_BACKEND=docker` in site.env).
Legacy Lyric script execution and AWEL Lyric operators fail closed in Docker
mode; they are not part of this deployment's supported execution path.
Notebook, browser automation and GPU execution are not included. New skills
may require additional packages baked into the sandbox image; installing
packages at runtime is not supported offline.

## OpenAI Compatibility

The existing `proxy/openai` provider is unchanged. To use it instead, replace
the LLM block in site.toml with the standard OpenAI example's block (api_base,
api_key, name), removing gateway-only app_key/app_secret/CA fields.
The customer gateway provider is selected independently as
`proxy/customer_gateway`.

## Operations

- Logs: `docker logs --tail 100 dbgpt`
- Stop: `docker stop dbgpt`
- Restart: `docker start dbgpt`
- Apply changed environment: stop/remove only the application container, then
  rerun start.sh. The bind-mounted data persists. Never remove data/ to
  upgrade.
- Health: `curl http://127.0.0.1:5670/api/health`

Offline smoke checks cover client loading, cached tokenizers and sandbox
analysis. Actual gateway, database login, schema reflection and query results
still require acceptance on the target network with real credentials.
Oracle 19.32 Basic is bundled for Oracle 11.2.0.4 Thick mode. Keep the Oracle
license/notice files shipped with Instant Client in the delivered image.
