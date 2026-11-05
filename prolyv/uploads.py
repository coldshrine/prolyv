"""Session photos. Everything is re-encoded with Pillow: metadata such as GPS is gone, and only real images survive."""

from __future__ import annotations

import shutil
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 50_000_000
MAX_PHOTOS = 4
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class PhotoError(ValueError):
    pass


def save_session_photo(media_dir: str, session_id: uuid.UUID, data: bytes) -> tuple[str, str]:
    """Returns (url, thumb_url) under /media. Raises PhotoError for anything that is not a usable picture."""
    if len(data) > MAX_BYTES:
        raise PhotoError("Photos can be up to 10 MB each.")
    try:
        with Image.open(BytesIO(data)) as raw:
            if raw.format not in ALLOWED_FORMATS:
                raise PhotoError("Use a JPEG, PNG or WebP photo.")
            if raw.width * raw.height > MAX_PIXELS:
                raise PhotoError("That photo is too large. Try a smaller one.")
            upright = ImageOps.exif_transpose(raw).convert("RGB")
    except PhotoError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise PhotoError("That file doesn't look like a photo.") from exc
    # a new image carries pixels only, no EXIF or other metadata
    clean = Image.new("RGB", upright.size)
    clean.paste(upright)
    folder = Path(media_dir) / "sessions" / session_id.hex
    folder.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex
    large = clean.copy()
    large.thumbnail((1200, 1200))
    large.save(folder / f"{name}-1200.webp", "WEBP", quality=82, method=4)
    clean.thumbnail((400, 400))
    clean.save(folder / f"{name}-400.webp", "WEBP", quality=75, method=4)
    base = f"/media/sessions/{session_id.hex}/{name}"
    return f"{base}-1200.webp", f"{base}-400.webp"


def delete_photo_files(media_dir: str, url: str, thumb_url: str | None) -> None:
    for value in (url, thumb_url):
        if value and value.startswith("/media/"):
            (Path(media_dir) / value.removeprefix("/media/")).unlink(missing_ok=True)


def delete_session_media(media_dir: str, session_id: uuid.UUID) -> None:
    shutil.rmtree(Path(media_dir) / "sessions" / session_id.hex, ignore_errors=True)
