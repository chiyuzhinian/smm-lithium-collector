from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
import yaml
from dotenv import load_dotenv

@dataclass(frozen=True)
class AppConfig:
    root: Path
    settings: dict
    selectors: dict
    login_url: str
    target_url: str
    username: str
    password: str

    @property
    def categories_mode(self) -> str:
        """'auto' = discover from page; 'manual' = use categories_items list."""
        cats = self.settings.get("categories", [])
        if isinstance(cats, dict):
            return cats.get("mode", "auto")
        return "manual"  # legacy list format

    @property
    def additional_sources(self) -> dict:
        """附加数据源配置。"""
        return self.settings.get("additional_sources", {})

    @property
    def categories_items(self) -> list:
        """Return explicit category items when mode is 'manual'."""
        cats = self.settings.get("categories", [])
        if isinstance(cats, dict):
            return cats.get("items", [])
        return cats  # legacy list format
    def path(self, key: str) -> Path:
        return self.root / self.settings["output"][key]

def load_config(root: Path | None = None) -> AppConfig:
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    load_dotenv(root / ".env")
    with (root / "config/settings.yaml").open(encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    with (root / "config/selectors.yaml").open(encoding="utf-8") as f:
        selectors = yaml.safe_load(f)
    if os.getenv("SMM_HEADLESS"):
        settings["browser"]["headless"] = os.getenv("SMM_HEADLESS", "true").lower() == "true"
    if os.getenv("SMM_TIMEOUT"):
        settings["browser"]["timeout_ms"] = int(os.environ["SMM_TIMEOUT"])
    for key in ("raw_dir", "processed_dir", "export_dir", "screenshot_dir"):
        (root / settings["output"][key]).mkdir(parents=True, exist_ok=True)
    (root / settings["output"]["database_path"]).parent.mkdir(parents=True, exist_ok=True)
    return AppConfig(root, settings, selectors, os.getenv("SMM_LOGIN_URL", ""),
                     os.getenv("SMM_TARGET_URL", ""), os.getenv("SMM_USERNAME", ""),
                     os.getenv("SMM_PASSWORD", ""))

