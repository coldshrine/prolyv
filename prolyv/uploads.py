"""Session photos. Everything is re-encoded with Pillow: metadata such as GPS is gone, and only real images survive."""

from __future__ import annotations

import shutil
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image

from prolyv.i18n import _

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 60_000_000  # JPEG: decoded at reduced scale, so a big phone photo costs little
MAX_PIXELS_OTHER = 8_000_000  # PNG, WebP and CMYK decode in full; so does a progressive JPEG, whose buffers cost far more
MAX_PIXELS_PROGRESSIVE = 12_000_000  # measured: 59 MP progressive reached 542 MB against a 300 MB service limit
MAX_PHOTOS = 4
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class PhotoError(ValueError):
    pass


def save_session_photo(media_dir: str, session_id: uuid.UUID, data: bytes) -> tuple[str, str]:
    """Returns (url, thumb_url) under /media. Raises PhotoError for anything that is not a usable picture."""
    if len(data) > MAX_BYTES:
        raise PhotoError(_("Photos can be up to 10 MB each."))
    try:
        with Image.open(BytesIO(data)) as raw:
            if raw.format not in ALLOWED_FORMATS:
                raise PhotoError(_("Use a JPEG, PNG or WebP photo."))
            plain = raw.mode in ("RGB", "L")
            limit = MAX_PIXELS if raw.format == "JPEG" and plain else MAX_PIXELS_OTHER
            if raw.format == "JPEG" and plain and raw.info.get("progressive"):
                limit = MAX_PIXELS_PROGRESSIVE
            if raw.width * raw.height > limit:
                raise PhotoError(_("That photo is too large. Try a smaller one."))
            orientation = raw.getexif().get(0x0112)
            picture = raw if plain else raw.convert("RGB")  # palettes and alpha don't resize well
            picture.thumbnail((1200, 1200))  # in place, and JPEG decodes at reduced scale
            clean = Image.new("RGB", picture.size)  # a new image carries pixels only, no EXIF or other metadata
            clean.paste(picture)
    except PhotoError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise PhotoError(_("That file doesn't look like a photo.")) from exc
    turn = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
            5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE, 8: Image.Transpose.ROTATE_90}.get(orientation)
    if turn is not None:
        clean = clean.transpose(turn)
    folder = Path(media_dir) / "sessions" / session_id.hex
    folder.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex
    clean.save(folder / f"{name}-1200.webp", "WEBP", quality=82, method=4)
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
    delete_story_files(media_dir, session_id)


def delete_story_files(media_dir: str, session_id: uuid.UUID) -> None:
    for path in (Path(media_dir) / "stories").glob(f"{session_id.hex}-*.png"):
        path.unlink(missing_ok=True)
