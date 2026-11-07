import resource
import sys
from io import BytesIO

_MAX_MEMORY = 256 * 1024 * 1024
_MAX_PAGES = 8
_MAX_TEXT_LENGTH = 64 * 1024


def main() -> int:
    resource.setrlimit(resource.RLIMIT_AS, (_MAX_MEMORY, _MAX_MEMORY))
    resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(sys.stdin.buffer.read()))
        if reader.is_encrypted:
            return 2
        if len(reader.pages) > _MAX_PAGES:
            return 4
        text = "\n".join(
            page.extract_text(
                extraction_mode="layout", layout_mode_space_vertically=False
            )
            for page in reader.pages
        )
    except MemoryError:
        return 4
    if len(text) > _MAX_TEXT_LENGTH:
        return 4
    if not text.strip():
        return 3
    sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
