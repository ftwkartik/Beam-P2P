"""Exports the wordlist and SAS symbol table as JSON.

Unlike `export_schema.py` (message shapes), this data can't be expressed as a JSON
Schema -- it's plain constant data both languages must share byte-for-byte
(docs/architecture.md §2, "The protocol is defined once."). The web client's
TypeScript modules are generated from these files (see web/scripts/codegen-data.mjs).

Run with: `python -m beam_protocol.export_data`
"""

from __future__ import annotations

import json
from pathlib import Path

from beam_protocol.sas_table import SAS_TABLE
from beam_protocol.wordlist import WORDLIST

#: protocol/schema/, resolved relative to this file so it works regardless of cwd.
SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "schema"


def export_all(output_dir: Path = SCHEMA_DIR) -> list[Path]:
    """Write wordlist.json and sas-table.json. Returns the paths written."""
    output_dir.mkdir(parents=True, exist_ok=True)

    wordlist_path = output_dir / "wordlist.json"
    wordlist_path.write_text(json.dumps(list(WORDLIST), indent=2) + "\n")

    sas_table_path = output_dir / "sas-table.json"
    sas_table_path.write_text(
        json.dumps([{"emoji": emoji, "name": name} for emoji, name in SAS_TABLE], indent=2) + "\n"
    )

    return [wordlist_path, sas_table_path]


def main() -> None:
    for path in export_all():
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
