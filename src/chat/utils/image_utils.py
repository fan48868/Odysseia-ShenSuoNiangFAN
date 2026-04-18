import io
import logging
from typing import Optional, Tuple

from PIL import Image

log = logging.getLogger(__name__)


TARGET_IMAGE_SIZE_BYTES = 7 * 1024 * 1024
MAX_IMAGE_SIZE_BYTES = 15 * 1024 * 1024
MAX_IMAGE_DIMENSION = 4096
MAX_QUALITY = 100
MIN_QUALITY = 50
QUALITY_STEP = 5
WEBP_METHOD = 4
MIN_IMAGE_DIMENSION = 128
RESIZE_RETRY_SCALE = 0.85
PASSTHROUGH_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

FORMAT_TO_MIME_TYPE = {
    "JPEG": "image/jpeg",
    "JPG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


def _get_image_mime_type(image_format: Optional[str]) -> str:
    return FORMAT_TO_MIME_TYPE.get(
        (image_format or "").upper(), "application/octet-stream"
    )


def _has_alpha_channel(img: Image.Image) -> bool:
    if "A" in img.getbands():
        return True
    if img.mode == "P":
        return "transparency" in img.info
    return False


def _encode_webp(img: Image.Image, *, quality: Optional[int] = None) -> bytes:
    output_buffer = io.BytesIO()
    try:
        save_kwargs = {"format": "WEBP", "method": WEBP_METHOD}
        save_kwargs["quality"] = quality if quality is not None else MAX_QUALITY
        img.save(output_buffer, **save_kwargs)
        return output_buffer.getvalue()
    finally:
        output_buffer.close()


def _downscale_image(img: Image.Image) -> Image.Image:
    new_width = max(MIN_IMAGE_DIMENSION, int(img.width * RESIZE_RETRY_SCALE))
    new_height = max(MIN_IMAGE_DIMENSION, int(img.height * RESIZE_RETRY_SCALE))
    if new_width == img.width and new_height == img.height:
        return img
    return img.resize((new_width, new_height), Image.Resampling.LANCZOS)


def sanitize_image_to_size_limit(
    image_bytes: bytes,
    *,
    target_size_bytes: int = TARGET_IMAGE_SIZE_BYTES,
    max_image_size_bytes: int = MAX_IMAGE_SIZE_BYTES,
    min_quality: int = MIN_QUALITY,
) -> Tuple[bytes, str]:
    """
    Normalize and compress image bytes to stay under the requested target size.

    If the image already satisfies the target and does not need resizing, keep the
    original encoding to avoid unnecessary recompression.
    """
    if not image_bytes:
        raise ValueError("Input image bytes cannot be empty.")
    if target_size_bytes <= 0:
        raise ValueError("target_size_bytes must be greater than 0.")
    if max_image_size_bytes <= 0:
        raise ValueError("max_image_size_bytes must be greater than 0.")
    if max_image_size_bytes < target_size_bytes:
        max_image_size_bytes = target_size_bytes
    if min_quality < 1:
        min_quality = 1
    if min_quality > MAX_QUALITY:
        min_quality = MAX_QUALITY

    original_byte_size = len(image_bytes)
    log.info(
        "Processing image | original_size_kb=%.2f | target_size_kb=%.2f | max_size_kb=%.2f",
        original_byte_size / 1024,
        target_size_bytes / 1024,
        max_image_size_bytes / 1024,
    )

    input_buffer = None
    try:
        input_buffer = io.BytesIO(image_bytes)
        with Image.open(input_buffer) as img:
            original_format = img.format
            original_mime_type = _get_image_mime_type(original_format)
            resized = False

            if img.width > MAX_IMAGE_DIMENSION or img.height > MAX_IMAGE_DIMENSION:
                log.info(
                    "Resizing image to fit max dimension | original_size=%s | max_dimension=%s",
                    img.size,
                    MAX_IMAGE_DIMENSION,
                )
                img.thumbnail(
                    (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
                    Image.Resampling.LANCZOS,
                )
                resized = True
                log.info("Image resized | new_size=%s", img.size)

            if (
                not resized
                and original_byte_size <= target_size_bytes
                and original_mime_type in PASSTHROUGH_MIME_TYPES
            ):
                log.info(
                    "Image already within target size, keeping original encoding | mime_type=%s",
                    original_mime_type,
                )
                return image_bytes, original_mime_type

            target_mode = "RGBA" if _has_alpha_channel(img) else "RGB"
            if img.mode != target_mode:
                img = img.convert(target_mode)

            processed_bytes = b""
            selected_quality = None
            current_img = img
            resize_attempt = 0

            while True:
                for quality in range(MAX_QUALITY, min_quality - 1, -QUALITY_STEP):
                    candidate_bytes = _encode_webp(current_img, quality=quality)
                    processed_bytes = candidate_bytes
                    log.debug(
                        "Trying WEBP quality=%s | size_kb=%.2f | dimensions=%sx%s | resize_attempt=%s",
                        quality,
                        len(candidate_bytes) / 1024,
                        current_img.width,
                        current_img.height,
                        resize_attempt,
                    )
                    if len(candidate_bytes) <= target_size_bytes:
                        selected_quality = quality
                        log.info(
                            "Compression succeeded within target | quality=%s | final_size_kb=%.2f | dimensions=%sx%s | resize_attempt=%s",
                            quality,
                            len(candidate_bytes) / 1024,
                            current_img.width,
                            current_img.height,
                            resize_attempt,
                        )
                        break

                if selected_quality is not None:
                    break

                if (
                    current_img.width <= MIN_IMAGE_DIMENSION
                    and current_img.height <= MIN_IMAGE_DIMENSION
                ):
                    log.warning(
                        "Image still exceeds target at minimum quality and minimum dimensions | size_kb=%.2f | target_kb=%.2f",
                        len(processed_bytes) / 1024,
                        target_size_bytes / 1024,
                    )
                    break

                resized_img = _downscale_image(current_img)
                if resized_img is current_img:
                    log.warning(
                        "Image cannot be downscaled further | size_kb=%.2f | target_kb=%.2f",
                        len(processed_bytes) / 1024,
                        target_size_bytes / 1024,
                    )
                    break

                current_img = resized_img
                resize_attempt += 1
                log.info(
                    "Retrying compression after downscale | dimensions=%sx%s | resize_attempt=%s",
                    current_img.width,
                    current_img.height,
                    resize_attempt,
                )

            if len(processed_bytes) > max_image_size_bytes:
                raise ValueError(
                    "Processed image still exceeds the allowed maximum size "
                    f"({len(processed_bytes) / 1024 / 1024:.2f} MB > "
                    f"{max_image_size_bytes / 1024 / 1024:.2f} MB)."
                )

            log.info(
                "Image processing complete | original_size_kb=%.2f | final_size_kb=%.2f",
                original_byte_size / 1024,
                len(processed_bytes) / 1024,
            )
            return processed_bytes, "image/webp"
    except Exception as e:
        log.error("Image processing failed: %s", e, exc_info=True)
        raise
    finally:
        if input_buffer is not None:
            try:
                input_buffer.close()
            except Exception:
                pass


def sanitize_image(image_bytes: bytes) -> Tuple[bytes, str]:
    return sanitize_image_to_size_limit(image_bytes)
