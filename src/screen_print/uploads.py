"""Safe, temporary handling for local screen-print film image uploads."""
from __future__ import annotations

import tempfile
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from src.screen_print.engine import FilmInspectionReport, inspect_film_image

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ALLOWED_IMAGE_FORMATS = {"PNG", "JPEG", "TIFF"}


def inspect_uploaded_image(payload: bytes) -> FilmInspectionReport:
    """Inspect an uploaded image in a temporary directory without retaining the original.

    Limits are intentionally conservative for this local MVP. The report is a
    preliminary heuristic, not a print-production approval.
    """
    if not payload:
        raise ValueError("الملف فارغ.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("حجم الملف يتجاوز الحد المحلي البالغ 25 MiB.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as image:
                if image.format not in ALLOWED_IMAGE_FORMATS:
                    raise ValueError("الصيغ المدعومة محلياً: PNG وJPEG وTIFF فقط.")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("ارفع صورة ثابتة بإطار واحد فقط، لا ملفاً متعدد الصفحات أو متحركاً.")
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("أبعاد الصورة تتجاوز الحد المحلي المسموح.")
                image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("رفضت الصورة لأن أبعادها تتجاوز حدود فك الضغط الآمن.") from exc
    except UnidentifiedImageError as exc:
        raise ValueError("لم أتمكن من التعرّف على محتوى الصورة.") from exc
    except OSError as exc:
        raise ValueError("الصورة تالفة أو يتعذر قراءتها.") from exc

    # لا نستخدم اسم الملف المرسل، ولا نحفظ الرفع بعد انتهاء الفحص.
    with tempfile.TemporaryDirectory(prefix="new-cela-film-") as temp_dir:
        image_path = Path(temp_dir) / "upload.bin"
        image_path.write_bytes(payload)
        return inspect_film_image(image_path, allowed_root=temp_dir)
