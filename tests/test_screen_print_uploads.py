"""اختبارات حدود رفع صور الأفلام في الواجهة المحلية."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from src.screen_print import uploads


def png_bytes(size: tuple[int, int] = (100, 100)) -> bytes:
    buffer = BytesIO()
    Image.new("L", size, color=255).save(buffer, format="PNG")
    return buffer.getvalue()


def test_inspect_uploaded_image_uses_temporary_file_and_removes_it(monkeypatch) -> None:
    payload = png_bytes()
    seen: dict[str, Path] = {}
    expected = object()

    def fake_inspect(path: str | Path, *, allowed_root: str | Path):
        path = Path(path)
        assert path.is_file()
        assert path.parent == Path(allowed_root)
        seen["path"] = path
        return expected

    monkeypatch.setattr(uploads, "inspect_film_image", fake_inspect)
    assert uploads.inspect_uploaded_image(payload) is expected
    assert not seen["path"].exists()


def test_inspect_uploaded_image_rejects_empty_and_oversized_payloads() -> None:
    with pytest.raises(ValueError, match="فارغ"):
        uploads.inspect_uploaded_image(b"")
    with pytest.raises(ValueError, match="25 MiB"):
        uploads.inspect_uploaded_image(b"x" * (uploads.MAX_UPLOAD_BYTES + 1))


def test_inspect_uploaded_image_rejects_unknown_or_oversized_image() -> None:
    with pytest.raises(ValueError, match="التعرّف"):
        uploads.inspect_uploaded_image(b"not an image")

    oversized = png_bytes((4500, 4500))
    with pytest.raises(ValueError, match="أبعاد الصورة"):
        uploads.inspect_uploaded_image(oversized)


def test_inspect_uploaded_image_rejects_unsupported_and_multiframe_formats() -> None:
    gif = BytesIO()
    Image.new("RGB", (20, 20), color="black").save(gif, format="GIF")
    with pytest.raises(ValueError, match="PNG وJPEG وTIFF"):
        uploads.inspect_uploaded_image(gif.getvalue())

    frames = [Image.new("RGB", (20, 20), color=color) for color in ("black", "white")]
    tiff = BytesIO()
    frames[0].save(tiff, format="TIFF", save_all=True, append_images=frames[1:])
    with pytest.raises(ValueError, match="إطار واحد"):
        uploads.inspect_uploaded_image(tiff.getvalue())
