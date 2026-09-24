"""Configuration management for WeddingHub Photobooth Uploader."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_LOCATIONS = [
    Path("/etc/weddinghub-photobooth/config.toml"),
    Path("./config.toml"),
]


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""


@dataclass
class Config:
    api_base_url: str
    device_token: str
    watch_directory: Path
    db_path: Path
    stability_delay: float = 2.0
    upload_timeout: float = 30.0
    max_retries: int = 0  # 0 means unlimited retries with backoff
    retry_initial_delay: float = 30.0
    retry_max_delay: float = 3600.0
    retry_jitter_factor: float = 0.1
    auth_error_retry_delay: float = 300.0
    scan_interval: float = 10.0
    log_level: str = "INFO"
    log_file: Path | None = None
    allow_insecure_http: bool = False

    def masked_device_token(self) -> str:
        """Return a safe masked token for logging or status displays."""
        token = self.device_token
        if not token:
            return "<none>"
        if len(token) <= 8:
            return "***"
        return f"{token[:4]}...{token[-4:]}"

    def safe_dict(self) -> dict[str, Any]:
        """Return config fields with secrets masked for CLI and logs."""
        return {
            "api_base_url": self.api_base_url,
            "device_token": self.masked_device_token(),
            "watch_directory": str(self.watch_directory),
            "db_path": str(self.db_path),
            "stability_delay": self.stability_delay,
            "upload_timeout": self.upload_timeout,
            "max_retries": self.max_retries,
            "retry_initial_delay": self.retry_initial_delay,
            "retry_max_delay": self.retry_max_delay,
            "retry_jitter_factor": self.retry_jitter_factor,
            "auth_error_retry_delay": self.auth_error_retry_delay,
            "scan_interval": self.scan_interval,
            "log_level": self.log_level,
            "log_file": str(self.log_file) if self.log_file else None,
            "allow_insecure_http": self.allow_insecure_http,
        }


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration from a specified path or standard locations."""
    resolved_path: Path | None = None

    if config_path is not None:
        p = Path(config_path)
        if not p.is_file():
            raise ConfigError(f"Specified configuration file does not exist: {p}")
        resolved_path = p
    elif os.environ.get("WEDDINGHUB_CONFIG_PATH"):
        env_path = Path(os.environ["WEDDINGHUB_CONFIG_PATH"])
        if not env_path.is_file():
            raise ConfigError(
                f"Configuration file from WEDDINGHUB_CONFIG_PATH does not exist: {env_path}"
            )
        resolved_path = env_path
    else:
        for candidate in DEFAULT_CONFIG_LOCATIONS:
            if candidate.is_file():
                resolved_path = candidate
                break

    if resolved_path is None:
        searched = [str(p) for p in DEFAULT_CONFIG_LOCATIONS]
        raise ConfigError(
            f"Configuration file not found. Searched locations: {', '.join(searched)}. "
            "Please create /etc/weddinghub-photobooth/config.toml or pass -c / path to config."
        )

    try:
        with open(resolved_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        raise ConfigError(f"Error reading TOML configuration file {resolved_path}: {e}") from e

    # Required fields validation
    required_keys = ["api_base_url", "device_token", "watch_directory", "db_path"]
    missing = [k for k in required_keys if not data.get(k)]
    if missing:
        raise ConfigError(
            f"Configuration file {resolved_path} is missing required fields: {', '.join(missing)}"
        )

    import urllib.parse

    api_base_url = str(data["api_base_url"]).rstrip("/")
    parsed_url = urllib.parse.urlparse(api_base_url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ConfigError(
            f"Invalid api_base_url '{api_base_url}'. Must be a valid URL starting with http:// or https://"
        )

    allow_insecure_http_val = data.get("allow_insecure_http", False)
    if not isinstance(allow_insecure_http_val, bool):
        raise ConfigError(f"allow_insecure_http must be a boolean, got {type(allow_insecure_http_val).__name__}")
    allow_insecure_http = bool(allow_insecure_http_val)

    if parsed_url.scheme == "http":
        hostname = (parsed_url.hostname or "").lower()
        local_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
        if hostname not in local_hosts and not allow_insecure_http:
            raise ConfigError(
                f"Insecure plain HTTP is not permitted for remote host '{hostname}'. "
                "HTTPS is required in production to protect the device Bearer token. "
                "For local testing/development, use localhost/127.0.0.1 or explicitly set allow_insecure_http = true in config.toml."
            )

    watch_directory = Path(data["watch_directory"])
    db_path = Path(data["db_path"])

    log_file_val = data.get("log_file")
    log_file = Path(log_file_val) if log_file_val else None

    return Config(
        api_base_url=api_base_url,
        device_token=str(data["device_token"]).strip(),
        watch_directory=watch_directory,
        db_path=db_path,
        stability_delay=float(data.get("stability_delay", 2.0)),
        upload_timeout=float(data.get("upload_timeout", 30.0)),
        max_retries=int(data.get("max_retries", 0)),
        retry_initial_delay=float(data.get("retry_initial_delay", 30.0)),
        retry_max_delay=float(data.get("retry_max_delay", 3600.0)),
        retry_jitter_factor=float(data.get("retry_jitter_factor", 0.1)),
        auth_error_retry_delay=float(data.get("auth_error_retry_delay", 300.0)),
        scan_interval=float(data.get("scan_interval", 10.0)),
        log_level=str(data.get("log_level", "INFO")).upper(),
        log_file=log_file,
        allow_insecure_http=allow_insecure_http,
    )
