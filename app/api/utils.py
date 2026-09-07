import logging
import os
import zipfile
from collections.abc import Iterable, Iterator
from io import BytesIO

import PIL
from fastapi.encoders import jsonable_encoder
from PIL import Image, ImageOps

from app.db.schemas.title import Scan

UPLOAD_VOLUME_PATH = os.getenv("SCANS_VOLUME_PATH")
logger = logging.getLogger(__name__)


def format_pages_integration(scans: list[Scan], filepaths: list[str]) -> list[dict]:
    """Overrides predicted pages with user edited pages if available, flattens the list."""
    formatted_pages = []
    for scan, original_filepath in zip(scans, filepaths):
        pages = (
            scan.user_edited_pages
            if scan.user_edited_pages is not None
            else scan.predicted_pages
        )
        pages = sorted(pages, key=lambda p: p.xc)  # left first

        if len(pages) == 2:
            page_types = ["left", "right"]
        else:
            page_types = ["single"] * len(pages)

        for page, page_type in zip(pages, page_types):
            formatted_pages.append(
                {
                    "filename": original_filepath,
                    "xc": page.xc,
                    "yc": page.yc,
                    "width": page.width,
                    "height": page.height,
                    "angle": page.angle,
                    "orientation": scan.orientation,
                    "type": page_type,
                }
            )
    return formatted_pages


def format_pages(scans: list[Scan]) -> list[dict]:
    """Overrides predicted pages with user edited pages if available."""
    formatted_scans = []
    for scan in scans:
        if scan.user_edited_pages is not None:
            edited = True
            pages = scan.user_edited_pages
        else:
            edited = False
            pages = scan.predicted_pages

        pages = sorted(pages, key=lambda p: p.xc)  # left first

        # Collect all flags from pages, store on scan level
        flags = set([flag for page in scan.predicted_pages for flag in page.flags])

        formatted_scans.append(
            {
                "_id": str(scan.id),
                "flags": flags,
                "orientation": scan.orientation,
                "scan_name": scan.scan_name,
                "pages": jsonable_encoder(pages),
                "edited": edited,
            }
        )
    return formatted_scans


def get_wrong_predictions(scans: list[Scan]) -> int:
    """Returns scans where user edited pages are present."""
    return [scan for scan in scans if scan.user_edited_pages is not None]


def format_predicted_pages(scans: list[Scan]) -> list[dict]:
    """Formats scans with ML generated pages only."""
    formatted_scans = []
    for scan in sorted(scans, key=lambda s: s.filename):
        flags = set([flag for page in scan.predicted_pages for flag in page.flags])
        formatted_scans.append(
            {
                "_id": str(scan.id),
                "flags": flags,
                "orientation": scan.orientation,
                "scan_name": scan.scan_name,
                "pages": jsonable_encoder(scan.predicted_pages),
            }
        )
    return formatted_scans


def resize_image(file_name, max_size: tuple = (160, 160)):
    """Resizes image bytes to fit within max_size while maintaining aspect ratio.

    Args:
        file (bytes): Original image bytes.
        max_size (tuple): Maximum width and height.

    Returns:
        bytes: Resized image bytes.
    """
    PIL.Image.MAX_IMAGE_PIXELS = None  # Disable DecompressionBombError for large images

    with Image.open(file_name) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail(max_size)
        output = BytesIO()
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
    image.save(output, format="JPEG")
    return output.getvalue()


def sniff_media_type(sig: bytes) -> str:
    """Sniffs the media type of a file based on its signature.

    Args:
        signature (bytes): File signature bytes.
    Returns:
        str: Media type string.
    """
    # JPEG
    if sig.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    # PNG
    if sig.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    # TIFF (little-endian / big-endian)
    if sig.startswith(b"II*\x00") or sig.startswith(b"MM\x00*"):
        return "image/tiff"

    return "application/octet-stream"


def save_scan_to_storage(title_id: str, file, filename: str) -> Scan:
    content = resize_image(file, (1400, 1400))
    scan_name = os.path.basename(filename)
    scan_name = scan_name.rsplit(".", 1)[0]

    # Save scan file to volume storage
    path = os.path.join(UPLOAD_VOLUME_PATH, title_id, f"{scan_name}.jpg")
    with open(path, "wb") as f:
        f.write(content)

    # Create scan object
    scan = Scan(filename=path, scan_name=scan_name)
    return scan


def remove_title_from_storage(title_id: str):
    """Delete all files associated with a title from storage volumes."""
    scans_path = os.path.join(UPLOAD_VOLUME_PATH, title_id)
    if not os.path.exists(scans_path):
        return

    files = os.listdir(scans_path)
    for filename in files:
        logger.debug(f"Deleting file '{filename}' from title '{title_id}'")
        os.remove(os.path.join(scans_path, filename))

    os.rmdir(scans_path)

    logger.info(
        f"Deleted {len(files)} files for title ID {title_id} from '{scans_path}'"
    )


class _ChunkSink:
    """Minimal write-only file object for ``zipfile`` that hands out written bytes.

    ``zipfile.ZipFile`` needs ``write`` / ``flush`` / ``tell`` on a non-seekable
    target; it then uses data descriptors instead of seeking back. Whatever was
    written since the last ``drain()`` is returned as one chunk.
    """

    def __init__(self):
        self._buf = bytearray()
        self._pos = 0

    def write(self, data: bytes) -> int:
        self._buf += data
        self._pos += len(data)
        return len(data)

    def flush(self) -> None:
        pass

    def tell(self) -> int:
        return self._pos

    def drain(self) -> bytes:
        chunk = bytes(self._buf)
        self._buf.clear()
        return chunk


def iter_scans_archive(scans: Iterable[dict]) -> Iterator[bytes]:
    """Streams a ZIP archive with one ``<scan_id>.jpg`` entry per scan.

    Files are stored uncompressed (they are JPEGs already), so memory use stays
    at roughly one scan image regardless of how many scans the title has.
    Missing files are skipped with a warning; entry names use the scan id so a
    client can pair them with the ``/scans`` response.
    """
    sink = _ChunkSink()
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for scan in scans:
            path = scan.get("filename")
            if not path or not os.path.isfile(path):
                logger.warning(f"Scan file missing, skipping in archive: {path}")
                continue
            archive.write(path, arcname=f"{scan['_id']}.jpg")
            yield sink.drain()
    # Central directory written on close.
    yield sink.drain()
