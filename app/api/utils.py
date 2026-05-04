from io import BytesIO
import logging
import os
import PIL
from fastapi.encoders import jsonable_encoder
from app.db.schemas.title import Scan
from PIL import Image, ImageOps


UPLOAD_VOLUME_PATH = os.getenv("SCANS_VOLUME_PATH")
logger = logging.getLogger(__name__)


def format_page_data_flat(scans: list[Scan], filepaths: list[str]) -> list[dict]:
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
                    "type": page_type,
                }
            )
    return formatted_pages


def format_page_data_list(scans: list[Scan]) -> list[dict]:
    """Overrides predicted pages with user edited pages if available."""
    formatted_scans = []
    for scan in sorted(scans, key=lambda s: s.filename):
        if scan.user_edited_pages is not None:
            edited = True
            pages = scan.user_edited_pages
        else:
            edited = False
            pages = scan.predicted_pages

        # Collect all flags from pages, store on scan level
        flags = set([flag for page in scan.predicted_pages for flag in page.flags])

        formatted_scans.append(
            {
                "_id": str(scan.id),
                "flags": flags,
                "scan_name": scan.scan_name,
                "pages": jsonable_encoder(pages),
                "edited": edited,
            }
        )
    return formatted_scans


def get_wrong_predictions(scans: list[Scan]) -> int:
    """Returns scans where user edited pages are present."""
    return [scan for scan in scans if scan.user_edited_pages is not None]


def format_predicted(scans: list[Scan]) -> list[dict]:
    """Formats scans with ML generated pages only."""
    formatted_scans = []
    for scan in sorted(scans, key=lambda s: s.filename):
        flags = set([flag for page in scan.predicted_pages for flag in page.flags])
        formatted_scans.append(
            {
                "_id": str(scan.id),
                "flags": flags,
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
