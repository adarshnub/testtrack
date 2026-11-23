"""Fetch the pinned English glyph data for the classic OCR engine."""

import argparse
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen

ASSETS = (
    (
        "eng.traineddata",
        "https://raw.githubusercontent.com/tesseract-ocr/tessdata/3.04.00/eng.traineddata",
        "c0515c9f1e0c79e1069fcc05c2b2f6a6841fb5e1082d695db160333c1154f06d",
    ),
    (
        "LICENSE",
        "https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/LICENSE",
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    ),
)


def fetch(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    for name, url, expected in ASSETS:
        target = destination / name
        if target.is_file() and sha256(target.read_bytes()).hexdigest() == expected:
            continue
        with urlopen(url, timeout=45) as response:
            data = response.read(24_000_001)
        if sha256(data).hexdigest() != expected:
            raise SystemExit(f"Checksum verification failed for {name}.")
        target.write_bytes(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    fetch(parser.parse_args().directory)
