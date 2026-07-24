"""On-device OCR via Apple Vision.

Turns a screenshot into raw text entirely offline — no cloud, no Tesseract.
Vision ships with macOS and is noticeably sharper than Tesseract on UI text.
The output is deliberately messy (sidebar labels, timestamps, notification
fragments); that's fine. We don't want clean extraction, we want enough for
Gemma to make a correct *judgment*.
"""

import Quartz
import Vision
from Foundation import NSURL

MAX_CHARS = 1500  # truncate per-sample; the model filters the noise


def ocr(path):
    """Extract text from an image file. Returns a single newline-joined string."""
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        return ""
    img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if img is None:
        return ""

    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(1)  # 1 = fast, 0 = accurate
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    handler.performRequests_error_([req], None)

    lines = []
    for obs in (req.results() or []):
        c = obs.topCandidates_(1)
        if c:
            lines.append(c[0].string())
    return "\n".join(lines)[:MAX_CHARS]


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("usage: python vision_ocr.py <image_path>")
        sys.exit(1)
    print(ocr(sys.argv[1]))
