# SIP Validator Service

A REST/OpenAPI service (Python/FastAPI) for validating Submission Information Packages (SIP, eCH-0160) and archival file formats. It uses [KOST-Val](https://github.com/KOST-CECO/KOST-Val) as its validation engine. The Windows build of KOST-Tools runs on Linux under Wine. The service is packaged as a Podman container.

> **Disclaimer:** This is a private, non-commercial educational project created for personal learning and exploration. It is not intended for production use. It is provided "as is", without warranty; the authors and copyright holders disclaim liability as described in the [MIT License](LICENSE).

```mermaid
flowchart LR
    C[Client] -- "POST /validations (SIP.zip)" --> API[FastAPI service]
    API -- job queue --> W[Worker]
    W -- "wine KOST-Val cmd --sip Z:\\...\\SIP.zip" --> K[KOST-Val in Wine]
    K -- XML log --> W
    C -- "GET /validations/{id}[/report]" --> API
```

## Repository layout

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI app and REST endpoints |
| `app/kostval.py` | Runs KOST-Val through Wine and parses the XML report |
| `app/jobs.py` | In-memory job queue that runs one validation at a time |
| `app/config.py` | Configuration from environment variables |
| `client/sip_validator_client.py` | Sample client (CLI and Python class) |
| `scripts/install-kost-tools.sh` | Downloads, checksums and installs the KOST-Tools MSI into the Wine prefix |
| `Containerfile`, `compose.yaml` | Podman image and compose setup |
| `deploy/sip-validator-api.container`, `deploy/sip-validator-data.volume` | Podman Quadlet units for systemd |
| `deploy/sip-validator-api.service` | systemd unit to run without a container |

## Running the service

### Podman Compose (development)

```bash
podman-compose up --build
```

The API listens on <http://localhost:8001>. Its API references are available at:

- [Swagger UI](http://localhost:8001/docs)
- [ReDoc](http://localhost:8001/redoc)
- [OpenAPI JSON specification](http://localhost:8001/openapi.json)

To require an API key:

```bash
SIP_VALIDATOR_API_KEY=change-me podman-compose up --build
```

### Podman Quadlet (production, systemd)

```bash
podman build -t localhost/sip-validator-api:latest -f Containerfile .
mkdir -p ~/.config/containers/systemd
cp deploy/sip-validator-api.container deploy/sip-validator-data.volume ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start sip-validator-api
loginctl enable-linger "$USER"   # start at boot without an interactive login
```

To enable the API key, create a Podman secret and uncomment the `Secret=` line in `sip-validator-api.container`:

```bash
printf '%s' 'change-me' | podman secret create sip-validator-api-key -
```

### Without a container

Run this on Debian or Ubuntu:

```bash
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install wine wine64 wine32 xvfb xauth python3-venv curl
bash scripts/install-kost-tools.sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
KOSTVAL_LAUNCHER="xvfb-run -a" .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
```

`deploy/sip-validator-api.service` is a systemd unit for this setup. It expects the code in `/opt/sip-validator-api`.

### Check the installation

```bash
curl http://localhost:8001/health
```

The response shows the KOST-Val command that was found. A `503` means the KOST-Val command-line tool was not found in the Wine prefix. In that case, set `KOSTVAL_CMD`.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `WINEPREFIX` | `~/.wine` | Wine prefix with KOST-Tools installed |
| `WINE_BIN` | `wine` | Wine executable |
| `KOSTVAL_LAUNCHER` | *(empty)* | Command prefix, e.g. `xvfb-run -a` for headless hosts |
| `KOSTVAL_CMD` | auto-discovered | KOST-Val CLI `.exe` or `.jar`, as a Linux path or a Windows path (`C:\...`) |
| `KOSTVAL_JAVA` | auto-discovered | `java.exe` to use when `KOSTVAL_CMD` is a `.jar` |
| `KOSTVAL_LOG_DIR` | `<prefix>/drive_c/users/$USER/.kost-val_2x/logs` | Directory where KOST-Val writes its logs |
| `SIP_VALIDATOR_DATA_DIR` | `./data` (`/data` in the container) | Uploaded SIPs and reports |
| `SIP_VALIDATOR_TIMEOUT` | `3600` | Maximum runtime of a single validation, in seconds |
| `SIP_VALIDATOR_MAX_UPLOAD_MB` | `4096` | Upload size limit |
| `SIP_VALIDATOR_API_KEY` | *(unset)* | If set, `/validations` endpoints require the `X-API-Key` header |

The former `KOSTVAL_DATA_DIR`, `KOSTVAL_TIMEOUT`, `KOSTVAL_MAX_UPLOAD_MB`, and `KOSTVAL_API_KEY` names remain supported for existing deployments; the `SIP_VALIDATOR_` names take precedence. The client also accepts the former `KOSTVAL_URL` and `KOSTVAL_API_KEY` variables as fallbacks.

Build arguments for the image: `KOST_TOOLS_VERSION`, `KOST_TOOLS_SHA256`. The install script also accepts `KOST_TOOLS_URL`.

## REST API

| Method | Path | Description |
|---|---|---|
| `POST` | `/validations` | Upload a SIP (`multipart/form-data`). Returns `202` with a job. |
| `GET` | `/validations` | List all jobs, newest first |
| `GET` | `/validations/{id}` | Job status and errors |
| `GET` | `/validations/{id}/report` | KOST-Val XML report (`409` while still running) |
| `DELETE` | `/validations/{id}` | Delete the job and its files |
| `GET` | `/health` | Service and KOST-Val discovery status |

Form fields for `POST /validations`:

| Field | Values | Default |
|---|---|---|
| `sip` | zipped SIP (`.zip` / `.zip64`) | required |
| `language` | `de`, `fr`, `it`, `en` | `de` |
| `log_type` | `xml`, `min`, `max` (`max` = xml + verbose) | `xml` |

Job status values: `queued`, `running`, `valid`, `invalid`, `error`.

Example job response:

```json
{
  "id": "3f1c2a9e-6a0b-4c1e-9a43-2f7d1e0c5b11",
  "status": "invalid",
  "sip_name": "SIP_20240101_KOST_eCH0160.zip",
  "language": "de",
  "log_type": "xml",
  "created_at": "2026-11-14T09:00:00Z",
  "started_at": "2026-11-14T09:00:01Z",
  "finished_at": "2026-11-14T09:00:42Z",
  "exit_code": 0,
  "message": null,
  "errors": [{ "module": "1D) Schemavalidierung", "message": "..." }],
  "report_url": "/validations/3f1c2a9e-6a0b-4c1e-9a43-2f7d1e0c5b11/report"
}
```

### Using curl

```bash
# submit
curl -F "sip=@SIP_20240101_KOST_eCH0160.zip" -F language=en http://localhost:8001/validations

# poll
curl http://localhost:8001/validations/<id>

# download the report
curl -o report.xml http://localhost:8001/validations/<id>/report
```

Add `-H "X-API-Key: change-me"` to each request when an API key is configured.

## Using the client

`client/sip_validator_client.py` uploads a SIP, waits for the result, prints the errors and saves the XML report.

### Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -r client/requirements.txt
```

On Windows: `python -m venv .venv` and `.venv\Scripts\pip install -r client\requirements.txt`.

### Command line

```bash
python client/sip_validator_client.py SIP_20240101_KOST_eCH0160.zip
```

| Option | Default | Description |
|---|---|---|
| `sip` | required | Path to the zipped SIP |
| `--url` | `$SIP_VALIDATOR_URL` or `http://localhost:8001` | Service base URL |
| `--api-key` | `$SIP_VALIDATOR_API_KEY` | API key, if the service requires one |
| `--language` | `de` | Report language: `de`, `fr`, `it`, `en` |
| `--log-type` | `xml` | `xml`, `min` or `max` |
| `--out` | `<sip>.kost-val.log.xml` | Where to save the report |
| `--poll` | `5` | Polling interval, in seconds |
| `--timeout` | `3600` | Maximum time to wait for the result, in seconds |

Example:

```bash
export SIP_VALIDATOR_URL=http://sip-validator.example.org:8001
export SIP_VALIDATOR_API_KEY=change-me
python client/sip_validator_client.py SIP_20240101_KOST_eCH0160.zip --language en --out report.xml
```

```text
Uploading SIP_20240101_KOST_eCH0160.zip to http://sip-validator.example.org:8001 ...
Job 3f1c2a9e-6a0b-4c1e-9a43-2f7d1e0c5b11 queued
  ... running
Result: INVALID
  - 1D) Schema validation: ...
Report saved to report.xml
```

Exit codes, usable in scripts and CI pipelines:

| Code | Meaning |
|---|---|
| `0` | SIP valid |
| `1` | SIP invalid |
| `2` | Validation error (e.g. KOST-Val failed, no report) |
| `3` | Timed out waiting for the result |

### As a Python library

```python
from pathlib import Path
from sip_validator_client import SipValidatorClient

client = SipValidatorClient("http://localhost:8001", api_key="change-me")
job = client.submit(Path("SIP_20240101_KOST_eCH0160.zip"), language="en")
job = client.wait(job["id"], poll_interval=5, timeout=3600)

print(job["status"], job["errors"])
if job["report_url"]:
    client.download_report(job["id"], Path("report.xml"))
```

## Notes and limitations

- KOST-Val shares its work and log directories, so validations run **one at a time**. Other jobs wait in the queue.
- Jobs are kept **in memory**. After a restart, existing job IDs are gone, but their reports remain in `SIP_VALIDATOR_DATA_DIR/jobs/<id>/`.
- Uploaded SIPs are deleted after validation. Reports are kept until the job is deleted with `DELETE /validations/{id}`.
- The uploaded SIP basename is passed to KOST-Val unchanged, because it checks the SIP naming convention. Filenames that would need sanitizing are rejected; directory components in the multipart filename are discarded.
- The service validates SIPs with KOST-Val's default configuration. To change it, edit the configuration files under `.kost-val_2x/configuration` in the Wine user profile.

## Licensing and third-party software

- The upstream [KOST-Val README](https://github.com/KOST-CECO/KOST-Val/blob/KOST-Tools_master/KOST-Val_README.md) identifies KOST-Val as GPL-3.0 software and notes that it embeds third-party components with their own license terms.
- By default, this project downloads and installs the MSI from the [KOST-Tools v1.3.1.0 release](https://github.com/KOST-CECO/KOST-Val/releases/tag/v1.3.1.0), which also includes a Java runtime and other tools. Review the release notes, upstream [license information](https://github.com/KOST-CECO/KOST-Val/blob/KOST-Tools_master/PH_Licence_KOST.pdf), and component licenses before redistributing an image built with this project.
- If you want to use KOST-Tools, [contact KOST-CECO](https://kost-ceco.ch/cms/kontakt.html) about applicable terms and support. The release notes explain support arrangements and that the optional PDF repair feature requires a separate installation and license code from KOST.
- The SIP Validator Service code is licensed under the [MIT License](LICENSE), which permits commercial use. This license does not replace the licenses of KOST-Val or other third-party software.

## Helpers

```
python client/sip_validator_client.py .\samples\SIP_20220328_KOST_eCH1.2F_1a-3d-IO.zip 
Invoke-RestMethod http://localhost:8001/validations/671050c0-9c00-44bb-8301-419ef695d6fa
Invoke-WebRequest http://localhost:8001/validations/671050c0-9c00-44bb-8301-419ef695d6fa/report -OutFile .\out\SIP_20220328_KOST_eCH1.2F_1a-3d-IO.zip.kost-val.log.xml
```
