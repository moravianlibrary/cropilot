from io import BytesIO
import logging
import os
from fastapi.encoders import jsonable_encoder
from app.db.schemas.title import Scan
from PIL import Image, ImageOps


RETRAIN_VOLUME_PATH = os.getenv("RETRAIN_VOLUME_PATH")
logger = logging.getLogger(__name__)


def format_page_data_flat(scans: list[Scan]) -> list[dict]:
    """Overrides predicted pages with user edited pages if available, flattens the list."""
    formatted_pages = []
    for scan in sorted(scans, key=lambda s: s.filename):
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
                    "filename": scan.filename,
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
    image = Image.open(file_name)
    image = ImageOps.exif_transpose(image)
    image.thumbnail(max_size)
    output = BytesIO()
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    image.save(output, format="JPEG")
    return output.getvalue()


def copy_images_for_retraining(id, filelist: list[str]) -> list[str]:
    """Copies images to retraining folder, returns new filelist.

    Args:
        id (str): Title ID to create a subfolder.
        filelist (list[str]): List of original file paths.
    Returns:
        list[str]: List of new file paths in retraining folder.
    """
    retrain_path = os.path.join(RETRAIN_VOLUME_PATH, str(id))
    logger.info(f"Creating retraining directory at {retrain_path}")
    os.makedirs(retrain_path, exist_ok=True)

    retrain_filelist = []
    for file_path in filelist:
        resized_image = resize_image(file_path, max_size=(960, 960))

        # Save as JPEG
        basename = os.path.basename(file_path)
        basename = basename.rsplit(".", 1)[0] + ".jpg"
        with open(os.path.join(retrain_path, basename), "wb") as f:
            f.write(resized_image)
        retrain_filelist.append(os.path.join(retrain_path, basename))

    return retrain_filelist


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
