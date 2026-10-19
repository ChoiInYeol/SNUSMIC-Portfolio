"""Text normalization shared by the transcription writer and cover readers."""
import re

MISSING_IMAGE = re.compile(r"^\s*!\[image\]\(image_\d+\.(?:png|jpe?g|webp)\)\s*$", re.I)


def strip_missing_images(text: str) -> str:
    """--no-images emits placeholders without assets; retain real/external image links."""
    lines, after_placeholder = [], False
    for line in text.splitlines():
        if MISSING_IMAGE.fullmatch(line):
            after_placeholder = True
            continue
        if after_placeholder and not line.strip():
            continue
        after_placeholder = False
        lines.append(line)
    return "\n".join(lines)
