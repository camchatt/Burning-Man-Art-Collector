from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class FormFile:
    filename: str
    content: bytes
    field_name: str


def parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, str], dict[str, FormFile]]:
    match = re.search(r"boundary=([^;]+)", content_type or "", flags=re.I)
    if not match:
        raise ValueError("multipart boundary missing")
    boundary = match.group(1).strip().strip('"').encode("ascii", errors="ignore")
    delimiter = b"--" + boundary
    fields: dict[str, str] = {}
    files: dict[str, FormFile] = {}

    for raw_part in body.split(delimiter):
        part = raw_part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.startswith(b"--"):
            continue
        header_blob, _, content = part.partition(b"\r\n\r\n")
        if not content and b"\n\n" in part:
            header_blob, _, content = part.partition(b"\n\n")
        content = content.rstrip(b"\r\n")
        headers = header_blob.decode("utf-8", errors="replace")
        name_match = re.search(r'name="([^"]+)"', headers)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', headers)
        if filename_match is not None:
            files[name] = FormFile(filename=filename_match.group(1) or "upload.csv", content=content, field_name=name)
        else:
            fields[name] = content.decode("utf-8", errors="replace")
    return fields, files
