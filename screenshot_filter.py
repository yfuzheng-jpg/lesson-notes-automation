"""Compare effective changed image area against the last *saved* screenshot."""
from __future__ import annotations

import math

DEFAULT_DIFFERENCE_PERCENT = 15.0
STABLE_DIFFERENCE_PERCENT = 1.0


def validate_difference_percent(value) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError("截图差异比例请填写 0–100 之间的数字。") from None
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("截图差异比例必须在 0–100% 之间；数值越大，保留的截图越少。")
    return value


def screenshot_fingerprint(image):
    import numpy as np
    from PIL import Image, ImageFilter

    # RGB catches color-only slide changes. Downsampling and a small blur suppress
    # encoding noise; a 24/255 dead band ignores insignificant pixel fluctuations.
    preview = image.convert("RGB").resize((320, 180), Image.Resampling.BILINEAR)
    return np.asarray(preview.filter(ImageFilter.GaussianBlur(0.5)), dtype=np.int16)


def difference_percent(current, previous) -> float:
    import numpy as np

    if current.shape != previous.shape:
        return 100.0
    changed = np.max(np.abs(current - previous), axis=2) >= 24
    return float(np.count_nonzero(changed) * 100 / changed.size)


def is_distinct(current, previous, minimum_percent: float) -> bool:
    return (previous is None or minimum_percent == 0
            or difference_percent(current, previous) >= minimum_percent)
