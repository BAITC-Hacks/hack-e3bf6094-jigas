"""Extract the three provided Parquet files for local development."""

from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "track_data" / "data (1).zip"
OUTPUT = ROOT / "data"
FILES = ("nodes.parquet", "edges.parquet", "transactions.parquet")


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    with ZipFile(ARCHIVE) as archive:
        for name in FILES:
            destination = OUTPUT / name
            destination.write_bytes(archive.read(f"data/{name}"))
            print(f"{destination}: {destination.stat().st_size} bytes")


if __name__ == "__main__":
    main()
