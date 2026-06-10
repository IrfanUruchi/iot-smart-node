import logging
import os


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s",
    )
    return logging.getLogger("iot-smart-node")
