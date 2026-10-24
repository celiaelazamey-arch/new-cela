"""Safe, temporary handling for local screen-print film image uploads."""
from __future__ import annotations

import tempfile
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from src.screen_print.engine import FilmInspectionReport, inspect_film_image

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ALLOWED_IMAGE_FORMATS = {"PNG", "JPEG", "TIFF"}


@dataclass(frozen=True)
class ImageUploadInfo:
    format: str
    width_px: int
    height_px: int
    has_alpha: bool


def validate_image_upload(payload: bytes) -> ImageUploadInfo:
    """Validate bounded, single-frame raster image bytes before processing."""
    if not payload:
        raise ValueError("الملف فارغ.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("حجم الملف يتجاوز الحد المحلي البالغ 25 MiB.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as image:
                image_format = str(image.format or "")
                if image_format not in ALLOWED_IMAGE_FORMATS:
                    raise ValueError("الصيغ المدعومة محلياً: PNG وJPEG وTIFF فقط.")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("ارفع صورة ثابتة بإطار واحد فقط، لا ملفاً متعدد الصفحات أو متحركاً.")
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("أبعاد الصورة تتجاوز الحد المحلي المسموح.")
                has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
                image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("رفضت الصورة لأن أبعادها تتجاوز حدود فك الضغط الآمن.") from exc
    except UnidentifiedImageError as exc:
        raise ValueError("لم أتمكن من التعرّف على محتوى الصورة.") from exc
    except OSError as exc:
        raise ValueError("الصورة تالفة أو يتعذر قراءتها.") from exc

    return ImageUploadInfo(image_format, width, height, has_alpha)


def inspect_uploaded_image(payload: bytes) -> FilmInspectionReport:
    """Inspect an uploaded image in a temporary directory without retaining the original.

    Limits are intentionally conservative for this local MVP. The report is a
    preliminary heuristic, not a print-production approval.
    """
    validate_image_upload(payload)

    # لا نستخدم اسم الملف المرسل، ولا نحفظ الرفع بعد انتهاء الفحص.
    with tempfile.TemporaryDirectory(prefix="new-cela-film-") as temp_dir:
        image_path = Path(temp_dir) / "upload.bin"
        image_path.write_bytes(payload)
        return inspect_film_image(image_path, allowed_root=temp_dir)
