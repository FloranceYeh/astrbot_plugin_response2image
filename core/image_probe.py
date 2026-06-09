try:
    from .messages import (
        image_dimensions_invalid,
        image_file_too_small,
        invalid_dimension_divisor,
        jpeg_dimensions_invalid,
        png_header_incomplete,
        unsupported_ref_image_dimensions,
        unsupported_webp_dimensions,
        webp_header_incomplete,
        webp_vp8_dimensions_invalid,
        webp_vp8l_dimensions_invalid,
    )
except ImportError:
    from core.messages import (
        image_dimensions_invalid,
        image_file_too_small,
        invalid_dimension_divisor,
        jpeg_dimensions_invalid,
        png_header_incomplete,
        unsupported_ref_image_dimensions,
        unsupported_webp_dimensions,
        webp_header_incomplete,
        webp_vp8_dimensions_invalid,
        webp_vp8l_dimensions_invalid,
    )


class ImageProbe:
    def format_size(self, size_bytes: int) -> str:
        if size_bytes >= 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        return f"{size_bytes / 1024:.1f} KB"

    def get_image_dimensions(self, data: bytes) -> tuple[int, int]:
        if len(data) < 10:
            raise ValueError(image_file_too_small())

        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            if len(data) < 24:
                raise ValueError(png_header_incomplete())
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            return self._validate_dimensions(width, height)

        if data.startswith((b"GIF87a", b"GIF89a")):
            width = int.from_bytes(data[6:8], "little")
            height = int.from_bytes(data[8:10], "little")
            return self._validate_dimensions(width, height)

        if data.startswith(b"\xff\xd8"):
            return self._parse_jpeg_dimensions(data)

        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return self._parse_webp_dimensions(data)

        raise ValueError(unsupported_ref_image_dimensions())

    def normalize_dimensions(self, width: int, height: int, *, divisor: int = 16) -> tuple[int, int]:
        if divisor <= 0:
            raise ValueError(invalid_dimension_divisor())
        return (
            self._nearest_multiple(width, divisor),
            self._nearest_multiple(height, divisor),
        )

    def _parse_jpeg_dimensions(self, data: bytes) -> tuple[int, int]:
        sof_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        index = 2
        while index < len(data):
            while index < len(data) and data[index] != 0xFF:
                index += 1
            while index < len(data) and data[index] == 0xFF:
                index += 1
            if index >= len(data):
                break

            marker = data[index]
            index += 1
            if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                continue
            if index + 2 > len(data):
                break

            segment_length = int.from_bytes(data[index:index + 2], "big")
            if segment_length < 2 or index + segment_length > len(data):
                break
            if marker in sof_markers:
                if segment_length < 7 or index + 7 > len(data):
                    break
                height = int.from_bytes(data[index + 3:index + 5], "big")
                width = int.from_bytes(data[index + 5:index + 7], "big")
                return self._validate_dimensions(width, height)
            index += segment_length

        raise ValueError(jpeg_dimensions_invalid())

    def _parse_webp_dimensions(self, data: bytes) -> tuple[int, int]:
        if len(data) < 30:
            raise ValueError(webp_header_incomplete())

        chunk = data[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return self._validate_dimensions(width, height)

        if chunk == b"VP8 ":
            if data[23:26] != b"\x9d\x01\x2a":
                raise ValueError(webp_vp8_dimensions_invalid())
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
            return self._validate_dimensions(width, height)

        if chunk == b"VP8L":
            if len(data) < 25 or data[20] != 0x2F:
                raise ValueError(webp_vp8l_dimensions_invalid())
            b0, b1, b2, b3 = data[21:25]
            width = 1 + (((b1 & 0x3F) << 8) | b0)
            height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return self._validate_dimensions(width, height)

        raise ValueError(unsupported_webp_dimensions())

    def _validate_dimensions(self, width: int, height: int) -> tuple[int, int]:
        if width <= 0 or height <= 0:
            raise ValueError(image_dimensions_invalid())
        return width, height

    def _nearest_multiple(self, value: int, divisor: int) -> int:
        if value <= divisor:
            return divisor
        lower = max(divisor, (value // divisor) * divisor)
        upper = max(divisor, ((value + divisor - 1) // divisor) * divisor)
        if value - lower <= upper - value:
            return lower
        return upper
