#!/usr/bin/env python3

"""Package selectable builtin models for the Ark installer."""

import io
import json
from pathlib import Path
import re
import sys
import tarfile


def build_templates(catalog_path: Path, archive_path: Path) -> None:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    templates = {}
    for model in catalog["models"]:
        if model["visibility"] != "list" or not model["supported_in_api"]:
            continue
        slug = model["slug"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", slug) or slug in templates:
            raise ValueError(f"Invalid or duplicate Ark model template name: {slug!r}")
        template = {"slug": "__ARK_ENDPOINT_ID__", **model}
        template["slug"] = "__ARK_ENDPOINT_ID__"
        template["display_name"] = slug
        templates[slug] = (
            json.dumps({"models": [template]}, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")

    if not templates:
        raise ValueError("No selectable builtin models for Ark")

    with tarfile.open(archive_path, "w:gz") as archive:
        for slug, payload in sorted(templates.items()):
            member = tarfile.TarInfo(f"ark-models/{slug}.json")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))


if __name__ == "__main__":
    build_templates(Path(sys.argv[1]), Path(sys.argv[2]))
