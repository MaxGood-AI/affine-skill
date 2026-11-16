# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Load image files for `![alt](path)` lines and name them the way AFFiNE names blobs.

AFFiNE stores an image as a workspace blob whose key is the URL-safe base64 (with `=`
padding) of the file's SHA-256 digest, and an `affine:image` block whose `prop:sourceId`
holds that key. The same bytes therefore always get the same key.
"""
import base64
import hashlib
import os

MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def blob_id(data):
    """The blob key AFFiNE computes for these bytes."""
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii")


def _matches_signature(mime, data):
    if mime == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if mime == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def load_image(path_text, base_dir=None):
    """Read one image file. Returns {"path", "source_id", "mime", "data"}.

    A relative path resolves against `base_dir` (the Markdown file's folder, or the
    current folder for inline text). Raises ValueError for a missing file, an
    unsupported type, or content that is not the image type its extension claims.
    """
    path = os.path.expanduser(path_text.strip())
    if not os.path.isabs(path):
        path = os.path.join(base_dir or os.getcwd(), path)
    path = os.path.normpath(path)
    ext = os.path.splitext(path)[1].lower()
    mime = MIME_BY_EXT.get(ext)
    if mime is None:
        raise ValueError(
            f"image {path_text!r}: unsupported type {ext or '(no extension)'!r}; "
            f"use one of {', '.join(sorted(MIME_BY_EXT))}"
        )
    if not os.path.isfile(path):
        raise ValueError(f"image file not found: {path}")
    with open(path, "rb") as f:
        data = f.read()
    if not data:
        raise ValueError(f"image file is empty: {path}")
    if not _matches_signature(mime, data):
        raise ValueError(f"image {path} is not a valid {mime} file (its content does not match its extension)")
    return {"path": path, "source_id": blob_id(data), "mime": mime, "data": data}
