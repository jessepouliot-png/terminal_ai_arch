import logging
import json
import os
from rich.logging import RichHandler
from rich.console import Console

class JsonFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Handle extra fields
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        return json.dumps(log_entry)

class StructuredLogger:
    """Wrapper to simplify structured logging with extra fields."""
    def __init__(self, logger):
        self.logger = logger
    
    def info(self, msg, **kwargs): self._log(logging.INFO, msg, kwargs)
    def warning(self, msg, **kwargs): self._log(logging.WARNING, msg, kwargs)
    def error(self, msg, **kwargs): self._log(logging.ERROR, msg, kwargs)
    def exception(self, msg, **kwargs): self._log(logging.ERROR, msg, kwargs, exc_info=True)
    def debug(self, msg, **kwargs): self._log(logging.DEBUG, msg, kwargs)

    def _log(self, level, msg, extra_fields, exc_info=False):
        self.logger.log(level, msg, extra={"extra_fields": extra_fields}, exc_info=exc_info)

from logging.handlers import RotatingFileHandler

def setup_logging(log_file):
    logger = logging.getLogger("agent_terminal")
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers if setup_logging is called multiple times
    if logger.handlers:
        return StructuredLogger(logger)
    
    # Keep RichHandler for pretty console output
    rich_handler = RichHandler(rich_tracebacks=True, console=Console(stderr=True))
    logger.addHandler(rich_handler)
    
    try:
        # Use RotatingFileHandler with JsonFormatter for file logging (10MB max, 5 backups)
        file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)
    except IOError:
        pass
    return StructuredLogger(logger)

