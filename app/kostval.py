"""Runs the Windows KOST-Val CLI through Wine and interprets its XML log."""

import asyncio
import getpass
import glob
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from defusedxml import ElementTree as DET

from .config import Settings

log = logging.getLogger(__name__)


class KostValError(RuntimeError):
    pass


@dataclass
class RunResult:
    exit_code: int | None
    output: str
    report: Path | None


def to_windows_path(path: Path) -> str:
    # Wine maps the Linux root filesystem to drive Z: by default.
    return "Z:" + str(path.resolve()).replace("/", "\\")


class KostValRunner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._command: tuple[list[str], Path] | None = None

    def _linux_path(self, path: str) -> Path:
        if len(path) > 2 and path[1] == ":":
            drive = path[0].lower()
            rest = path[2:].replace("\\", "/").lstrip("/")
            return self.settings.wineprefix / "dosdevices" / f"{drive}:" / rest
        return Path(path)

    def _discover_cli(self) -> Path:
        drive_c = self.settings.wineprefix / "drive_c"
        candidates: list[Path] = []
        for root in (drive_c / "Program Files", drive_c / "Program Files (x86)", drive_c / "users"):
            if not root.is_dir():
                continue
            for p in root.rglob("*"):
                name = p.name.lower()
                if "kost-val" in name and "cmd" in name and p.suffix.lower() in (".exe", ".jar"):
                    candidates.append(p)
        if not candidates:
            raise KostValError(
                f"KOST-Val CLI not found in {drive_c}. Install KOST-Tools or set KOSTVAL_CMD."
            )
        # Prefer a native launcher (bundles its JRE) over a bare jar.
        candidates.sort(key=lambda p: (p.suffix.lower() != ".exe", len(str(p))))
        return candidates[0]

    def _discover_java(self, jar: Path) -> Path:
        candidates: list[Path] = []
        for parent in list(jar.parents)[:3]:
            for java in parent.rglob("java.exe"):
                if java.parent.name.lower() == "bin":
                    candidates.append(java)
        if candidates:
            # KOST-Tools also bundles Java 8 runtimes for helper applications.
            # KOST-Val itself needs Java 11+, so prefer the shared Liberica runtime.
            candidates.sort(
                key=lambda java: ("liberica_jre" not in str(java).lower(), len(str(java)))
            )
            return candidates[0]
        raise KostValError(f"No bundled java.exe found near {jar}. Set KOSTVAL_JAVA.")

    def command(self) -> tuple[list[str], Path]:
        """Returns (argv prefix, working directory) used to invoke the KOST-Val CLI."""
        if self._command is None:
            s = self.settings
            target = self._linux_path(s.kostval_cmd) if s.kostval_cmd else self._discover_cli()
            if not target.is_file():
                raise KostValError(f"KOST-Val CLI not found: {target}")
            if target.suffix.lower() == ".jar":
                java = self._linux_path(s.java_exe) if s.java_exe else self._discover_java(target)
                argv = [*s.launcher, s.wine_bin, to_windows_path(java), "-jar", to_windows_path(target)]
            else:
                argv = [*s.launcher, s.wine_bin, to_windows_path(target)]
            # KOST-Val resolves its resources relative to the install directory.
            self._command = (argv, target.parent)
            log.info("Using KOST-Val command: %s (cwd=%s)", argv, target.parent)
        return self._command

    def log_dir(self) -> Path:
        if self.settings.log_dir:
            return self.settings.log_dir
        user = os.environ.get("USER") or getpass.getuser()
        return self.settings.wineprefix / "drive_c" / "users" / user / ".kost-val_2x" / "logs"

    async def _kill_wine(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "wineserver", "-k", env={**os.environ, "WINEPREFIX": str(self.settings.wineprefix)}
        )
        await proc.wait()

    async def validate_sip(self, sip: Path, language: str, log_type: str) -> RunResult:
        argv, cwd = self.command()
        argv = [*argv, "--sip", to_windows_path(sip), f"--{language}", f"--{log_type}"]

        log_dir = self.log_dir()
        pattern = glob.escape(sip.name) + "*.kost-val.log.xml"
        for stale in log_dir.glob(pattern):
            stale.unlink(missing_ok=True)

        env = {
            **os.environ,
            "WINEPREFIX": str(self.settings.wineprefix),
            "WINEDEBUG": os.environ.get("WINEDEBUG", "-all"),
        }
        log.info("Running: %s", argv)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self.settings.timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            # The JVM lives inside wineserver and survives killing the wine launcher.
            await self._kill_wine()
            raise KostValError(f"KOST-Val timed out after {self.settings.timeout_seconds}s")

        reports = sorted(log_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
        return RunResult(
            exit_code=proc.returncode,
            output=out.decode("utf-8", errors="replace"),
            report=reports[-1] if reports else None,
        )


def summarize_report(report: Path) -> tuple[str, list[dict[str, str]]]:
    """Returns ("valid" | "invalid" | "unknown", [errors]) for a KOST-Val XML log."""
    root = DET.parse(report).getroot()

    status = "unknown"
    validation = root.find("./Sip/Validation")
    if validation is not None:
        if validation.find("Valid") is not None:
            status = "valid"
        elif validation.find("Invalid") is not None:
            status = "invalid"

    errors = []
    for err in root.iter("Error"):
        messages = [m.text.strip() for m in err.iter("Message") if m.text and m.text.strip()]
        errors.append({"module": (err.findtext("Modul") or "").strip(), "message": " ".join(messages)})
    return status, errors
