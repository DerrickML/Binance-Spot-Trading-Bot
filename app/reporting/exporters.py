"""Report exporters — JSON, CSV, and Markdown output."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_OUTPUT_DIR = "outputs"


def ensure_output_dir(subdir: str = "") -> Path:
    """Ensure output directory exists and return its path."""
    path = Path(DEFAULT_OUTPUT_DIR) / subdir if subdir else Path(DEFAULT_OUTPUT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_json(data: Any, filename: str, subdir: str = "") -> str:
    """Export data to JSON file."""
    out_dir = ensure_output_dir(subdir)
    filepath = out_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info("exported_json", file=str(filepath))
    return str(filepath)


def export_csv(rows: list[dict[str, Any]], filename: str, subdir: str = "") -> str:
    """Export data to CSV file."""
    if not rows:
        logger.warning("export_csv_empty", filename=filename)
        return ""

    out_dir = ensure_output_dir(subdir)
    filepath = out_dir / filename

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    logger.info("exported_csv", file=str(filepath), rows=len(rows))
    return str(filepath)


def export_markdown(content: str, filename: str, subdir: str = "") -> str:
    """Export markdown content to file."""
    out_dir = ensure_output_dir(subdir)
    filepath = out_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("exported_markdown", file=str(filepath))
    return str(filepath)
