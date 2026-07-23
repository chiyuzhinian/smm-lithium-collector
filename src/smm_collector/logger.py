import logging
from datetime import date
from pathlib import Path

class ErrorOnly(logging.Filter):
    def filter(self, record): return record.levelno >= logging.ERROR

def setup_logging(root: Path) -> logging.Logger:
    log_dir = root / "logs"; log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("smm_collector"); logger.setLevel(logging.INFO)
    if logger.handlers: return logger
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    all_h = logging.FileHandler(log_dir / f"collector_{date.today()}.log", encoding="utf-8")
    err_h = logging.FileHandler(log_dir / f"error_{date.today()}.log", encoding="utf-8")
    err_h.addFilter(ErrorOnly())
    console = logging.StreamHandler()
    for h in (all_h, err_h, console): h.setFormatter(fmt); logger.addHandler(h)
    return logger

