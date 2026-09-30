"""Validate static workspace image inputs without changing their original bytes."""

from __future__ import annotations

import warnings
from io import BytesIO
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from my_agents.document_workspace.formats import GENERIC_UPLOAD_MIME_TYPES, IMAGE_INPUT_FORMATS


class ImageAttachmentValidationError(ValueError):
    """An upload cannot be treated as a supported static image."""


def validate_image_attachment(file: BinaryIO, extension: str, content_type: str) -> str:
    """Decode a bounded upload, reject animation/mismatch, and return its canonical MIME."""
    expected_format, expected_mime = IMAGE_INPUT_FORMATS[extension]
    try:
        if content_type not in GENERIC_UPLOAD_MIME_TYPES and content_type != expected_mime:
            raise ImageAttachmentValidationError("Image content type does not match its extension")
        file.seek(0)
        # Pillow is already installed by the project's Docling/office dependencies.
        # Work on an owned buffer so decoding/verification cannot close the upload stream.
        with BytesIO(file.read()) as buffer, warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(buffer, formats=("JPEG", "PNG", "WEBP", "GIF")) as image:
                if image.format != expected_format:
                    raise ImageAttachmentValidationError("Image bytes do not match its extension")
                if getattr(image, "is_animated", False):
                    raise ImageAttachmentValidationError(
                        "Animated images are not supported; use a static image"
                    )
                image.verify()
            buffer.seek(0)
            with Image.open(buffer, formats=(expected_format,)) as image:
                image.load()
        return expected_mime
    except ImageAttachmentValidationError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ImageAttachmentValidationError(
            "Image attachment is invalid or exceeds safe image limits"
        ) from exc
    finally:
        file.seek(0)
