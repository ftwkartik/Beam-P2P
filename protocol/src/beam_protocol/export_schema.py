"""Exports JSON Schema for the wire protocol types.

The web client's TypeScript types are generated from these files with
json-schema-to-typescript (see web/package.json's `codegen` script and
docs/architecture.md §2, "The protocol is defined once."). Pydantic is the single
source of truth; nothing here is meant to be hand-edited on the TypeScript side.

Run with: `python -m beam_protocol.export_schema`
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from beam_protocol.peer import PeerMessage
from beam_protocol.signaling import ClientMessage, ServerMessage

#: protocol/schema/, resolved relative to this file so it works regardless of cwd.
SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "schema"

_EXPORTS: dict[str, object] = {
    "client-message": ClientMessage,
    "server-message": ServerMessage,
    "peer-message": PeerMessage,
}


def export_all(output_dir: Path = SCHEMA_DIR) -> list[Path]:
    """Write one `<name>.schema.json` file per exported type. Returns the paths written."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, type_ in _EXPORTS.items():
        schema = TypeAdapter(type_).json_schema()
        # A stable title makes json-schema-to-typescript name the generated type
        # sensibly (e.g. "ClientMessage") instead of an anonymous union type.
        schema["title"] = "".join(part.capitalize() for part in name.split("-"))
        path = output_dir / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


def main() -> None:
    for path in export_all():
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
