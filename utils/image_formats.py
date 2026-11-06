"""Image output validation and byte-based format detection."""
from io import BytesIO
from typing import Any

from PIL import Image


def generation_options(body: dict[str, Any]) -> dict[str, Any]:
    output_format = str(body.get("output_format") or "png").lower()
    if output_format not in {"png", "gif"}:
        raise ValueError("output_format must be png or gif")
    frames, duration = body.get("frame_count"), body.get("frame_duration_ms")
    if output_format == "png":
        if frames is not None or duration is not None:
            raise ValueError("frame_count and frame_duration_ms require output_format=gif")
        return {"output_format": "png"}
    if body.get("stream"):
        raise ValueError("GIF generation does not support stream=true")
    if body.get("images") or body.get("image") or body.get("mask"):
        raise ValueError("GIF output is supported for generation only")
    if body.get("account_pool") == "gptfree":
        raise ValueError("GIF generation is not supported by the gptFree account pool")
    frames = 9 if frames is None else frames
    duration = 200 if duration is None else duration
    if isinstance(frames, bool) or not isinstance(frames, int) or not 2 <= frames <= 16:
        raise ValueError("frame_count must be an integer between 2 and 16")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 20 <= duration <= 2000:
        raise ValueError("frame_duration_ms must be an integer between 20 and 2000")
    size = str(body.get("size") or "auto")
    if size != "auto":
        try:
            width, height = map(int, size.split("x"))
            valid = width > 0 and height > 0 and width % 16 == height % 16 == 0
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("GIF size must be auto or positive WIDTHxHEIGHT multiples of 16")
    return {"output_format": "gif", "frame_count": frames, "frame_duration_ms": duration}


def image_metadata(data: bytes) -> dict[str, Any]:
    try:
        with Image.open(BytesIO(data)) as image:
            fmt = (image.format or "PNG").lower()
            metadata = {"output_format": fmt, "mime_type": Image.MIME.get(image.format, "image/png")}
            if fmt == "gif":
                metadata.update(frame_count=image.n_frames, frame_duration_ms=int(image.info.get("duration", 0)))
            return metadata
    except Exception:
        return {"output_format": "png", "mime_type": "image/png"}
