import os
import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    wine_bin: str
    launcher: list[str]
    wineprefix: Path
    kostval_cmd: str | None
    java_exe: str | None
    log_dir: Path | None
    data_dir: Path
    timeout_seconds: int
    max_upload_bytes: int
    api_key: str | None


def load_settings() -> Settings:
    env = os.environ.get
    return Settings(
        wine_bin=env("WINE_BIN", "wine"),
        # e.g. "xvfb-run -a" when running headless
        launcher=shlex.split(env("KOSTVAL_LAUNCHER", "")),
        wineprefix=Path(env("WINEPREFIX", str(Path.home() / ".wine"))),
        # Path (Linux or Windows style) to the KOST-Val CLI .exe or .jar; auto-discovered if unset
        kostval_cmd=env("KOSTVAL_CMD") or None,
        java_exe=env("KOSTVAL_JAVA") or None,
        log_dir=Path(env("KOSTVAL_LOG_DIR")) if env("KOSTVAL_LOG_DIR") else None,
        data_dir=Path(env("SIP_VALIDATOR_DATA_DIR", env("KOSTVAL_DATA_DIR", "./data"))),
        timeout_seconds=int(env("SIP_VALIDATOR_TIMEOUT", env("KOSTVAL_TIMEOUT", "3600"))),
        max_upload_bytes=int(env("SIP_VALIDATOR_MAX_UPLOAD_MB", env("KOSTVAL_MAX_UPLOAD_MB", "4096")))
        * 1024
        * 1024,
        api_key=env("SIP_VALIDATOR_API_KEY") or env("KOSTVAL_API_KEY") or None,
    )
