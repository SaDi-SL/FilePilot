import logging
from pathlib import Path


def setup_logging(log_file: str) -> None:
    """
    Set up logging safely.
    This function can be called multiple times — duplicate handlers will not be added.
    """
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Ensure no duplicate FileHandler is added for the same file
    for handler in root_logger.handlers:
        if isinstance(handler, logging.FileHandler):
            if handler.baseFilename == str(log_path.resolve()):
                return

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    root_logger.addHandler(file_handler)
