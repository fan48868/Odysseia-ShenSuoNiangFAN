import io
import logging
from PIL import Image
from typing import Optional, Tuple

log = logging.getLogger(__name__)


# --- 压缩策略常量 ---
TARGET_IMAGE_SIZE_BYTES = 7 * 1024 * 1024  # 7 MB (目标大小上限)
MAX_IMAGE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB (硬性物理上限)
MAX_IMAGE_DIMENSION = 4096  # 4096 像素 (最大尺寸)
MAX_QUALITY = 100  # 可尝试的最高质量
MIN_QUALITY = 50  # 最低可接受质量
QUALITY_STEP = 5  # 每次迭代降低的质量值
WEBP_METHOD = 4  # 在编码速度与压缩率之间取平衡
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
}


def _get_image_mime_type(image_format: Optional[str]) -> str:
    return FORMAT_TO_MIME_TYPE.get((image_format or "").upper(), "image/png")


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


def sanitize_image(image_bytes: bytes) -> Tuple[bytes, str]:
    """
    对输入的图片字节数据进行智能预处理和压缩。
    - **如果图片已满足要求**: 在无需缩放时保留原始编码，避免无意义重压缩。
    - **如果需要重新编码**: 直接查找满足 7MB 的最高质量 WebP，避免慢速无损编码阻塞。
    - **最终检查**: 任何情况下，处理后的图片都不能超过 15MB 的物理上限。

    内存优化：确保所有 BytesIO 缓冲区在使用后立即关闭，防止内存泄漏。
    """
    if not image_bytes:
        raise ValueError("输入的图片字节数据不能为空。")

    original_byte_size = len(image_bytes)
    log.info(f"开始处理图片，原始大小: {original_byte_size / 1024:.2f} KB。")

    input_buffer = None
    try:
        # 使用上下文管理器确保输入缓冲区被正确关闭
        input_buffer = io.BytesIO(image_bytes)

        with Image.open(input_buffer) as img:
            # --- 1. 尺寸调整 (对所有图片都执行) ---
            original_format = img.format
            original_mime_type = _get_image_mime_type(original_format)
            resized = False
            if img.width > MAX_IMAGE_DIMENSION or img.height > MAX_IMAGE_DIMENSION:
                log.info(
                    f"图片尺寸 {img.size} 超过最大限制 {MAX_IMAGE_DIMENSION}px，将进行缩放。"
                )
                img.thumbnail(
                    (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS
                )
                resized = True
                log.info(f"图片已缩放至: {img.size}")

            # --- 2. 已满足目标时优先保留原始编码 ---
            if (
                not resized
                and original_byte_size <= TARGET_IMAGE_SIZE_BYTES
                and original_mime_type in PASSTHROUGH_MIME_TYPES
            ):
                log.info("图片已满足大小要求且无需缩放，保留原始编码。")
                return image_bytes, original_mime_type

            # --- 3. 仅在确有需要时重新编码 ---
            target_mode = "RGBA" if _has_alpha_channel(img) else "RGB"
            if img.mode != target_mode:
                img = img.convert(target_mode)

            # --- 3. 从高到低查找满足目标的最高质量 ---
            log.info("图片需要重新编码，开始寻找满足限制的最高质量版本。")
            processed_bytes = b""
            selected_quality = None
            for quality in range(MAX_QUALITY, MIN_QUALITY - 1, -QUALITY_STEP):
                candidate_bytes = _encode_webp(img, quality=quality)

                log.debug(
                    f"尝试使用质量 {quality} 进行压缩，大小为: {len(candidate_bytes) / 1024:.2f} KB。"
                )

                processed_bytes = candidate_bytes
                if len(candidate_bytes) <= TARGET_IMAGE_SIZE_BYTES:
                    selected_quality = quality
                    log.info(
                        f"压缩成功，找到满足目标要求的最高质量版本。最终质量: {quality}。"
                    )
                    break

            if selected_quality is None:
                log.warning(
                    f"即便使用最低质量 {MIN_QUALITY}，文件大小 ({len(processed_bytes) / 1024:.2f} KB) "
                    f"仍未达到 {TARGET_IMAGE_SIZE_BYTES / 1024 / 1024:.2f} MB 的目标。"
                )

            # --- 4. 最终检查 (对所有图片都执行) ---
            if len(processed_bytes) > MAX_IMAGE_SIZE_BYTES:
                raise ValueError(
                    f"图片经过处理后大小 ({len(processed_bytes) / 1024 / 1024:.2f} MB) "
                    f"仍然超过了物理上限 {MAX_IMAGE_SIZE_BYTES / 1024 / 1024:.0f} MB。"
                )

            log.info(
                f"图片处理完成。原始大小: {original_byte_size / 1024:.2f} KB -> "
                f"处理后大小: {len(processed_bytes) / 1024:.2f} KB."
            )

            return processed_bytes, "image/webp"
    except Exception as e:
        log.error(f"图片处理过程中发生严重错误: {e}", exc_info=True)
        raise
    finally:
        # 确保所有缓冲区都被关闭
        if input_buffer is not None:
            try:
                input_buffer.close()
            except Exception:
                pass
