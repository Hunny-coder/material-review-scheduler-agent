"""Redact credential fields from archived public JSON/JSONL without changing business results."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_FIELDS = {"confirmation_token", "token", "api_key", "MODEL_API_KEY"}


def scrub(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key in SECRET_FIELDS and item else scrub(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def main():
    changed = []
    for path in sorted((ROOT / "examples").rglob("*")):
        if path.suffix == ".json":
            original = json.loads(path.read_text(encoding="utf-8"))
            sanitized = scrub(original)
            if sanitized != original:
                path.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                changed.append(str(path.relative_to(ROOT)))
        elif path.suffix == ".jsonl":
            original = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            sanitized = [scrub(record) for record in original]
            if sanitized != original:
                path.write_text(
                    "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in sanitized),
                    encoding="utf-8",
                )
                changed.append(str(path.relative_to(ROOT)))
    for path in (ROOT / "examples/runs").rglob("run-metadata.json"):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["public_evidence_redaction"] = ["confirmation_token", "workspace_prefix", "hostname"]
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Redacted credential fields in {len(changed)} archived files; business states remain unchanged.")


if __name__ == "__main__":
    main()
