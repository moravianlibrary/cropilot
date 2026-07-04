import logging
import os

import numpy as np

from app.db.schemas.title import Anomaly, Page, Scan

logger = logging.getLogger(__name__)


# --- Tunables (env-overridable) --------------------------------------------
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# Leading/trailing scans (cover, paper wrapper, back cover) are structurally
# different and are exempt from single-page and dimension checks.
STRUCTURAL_LEAD = _env_int("ANOMALY_STRUCTURAL_LEAD", 1)
STRUCTURAL_TAIL = _env_int("ANOMALY_STRUCTURAL_TAIL", 1)

# Low confidence: adaptive per-title threshold (median - k*MAD), never above base.
LOW_CONF_BASE = _env_float("ANOMALY_LOW_CONF_BASE", 0.5)
LOW_CONF_MAD_K = _env_float("ANOMALY_LOW_CONF_MAD_K", 3.0)

# Dimensions: robust outlier (median +- k*MAD) + absolute min-area floor.
DIM_MAD_K = _env_float("ANOMALY_DIM_MAD_K", 3.5)
DIM_MIN_AREA = _env_float("ANOMALY_DIM_MIN_AREA", 0.02)  # <2% of scan ~ spurious box

# Missing page: a single page narrow enough to leave room for a second one.
MISSING_MAX_WIDTH = _env_float("ANOMALY_MISSING_MAX_WIDTH", 0.6)

# Overlap: intersection-over-min-area (captures duplicate/nested detections).
OVERLAP_MIN_RATIO = _env_float("ANOMALY_OVERLAP_MIN_RATIO", 0.2)

# Bad spread split (2-page scans): width asymmetry / off-center seam.
SPLIT_ASYMMETRY = _env_float("ANOMALY_SPLIT_ASYMMETRY", 0.35)
SPLIT_SEAM_OFFSET = _env_float("ANOMALY_SPLIT_SEAM_OFFSET", 0.12)


# --- Helpers ----------------------------------------------------------------
def _median_mad(values: list[float]) -> tuple[float, float]:
    """Median and Median Absolute Deviation - a robust spread estimate."""
    arr = np.asarray(values, dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    return med, mad


def _structural_indices(n: int) -> set[int]:
    """Indices of the leading/trailing scans treated as cover/wrapper/back cover."""
    idx = set(range(min(STRUCTURAL_LEAD, n)))
    idx.update(range(max(0, n - STRUCTURAL_TAIL), n))
    return idx


def _page_xyxy(page: Page) -> tuple[float, float, float, float]:
    """Normalized (0-1) corner coordinates of a page box."""
    return (
        page.xc - page.width / 2,
        page.yc - page.height / 2,
        page.xc + page.width / 2,
        page.yc + page.height / 2,
    )


def _intersection_over_min(a: Page, b: Page) -> float:
    """Intersection area over the smaller box area (0-1)."""
    ax1, ay1, ax2, ay2 = _page_xyxy(a)
    bx1, by1, bx2, by2 = _page_xyxy(b)
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    smaller = min(a.width * a.height, b.width * b.height)
    return inter / smaller if smaller > 0 else 0.0


# --- Flags ------------------------------------------------------------------
def flag_low_confidence(scans: list[Scan]) -> list[Scan]:
    """Flags pages whose confidence is a low outlier for this title.

    Uses an adaptive threshold (median - k*MAD) capped at ``LOW_CONF_BASE`` so a
    title where every page is uncertain doesn't get every page flagged.
    """
    confidences = [p.confidence for s in scans for p in s.predicted_pages]
    if not confidences:
        return scans

    med, mad = _median_mad(confidences)
    threshold = min(LOW_CONF_BASE, med - LOW_CONF_MAD_K * mad)

    for scan in scans:
        for page in scan.predicted_pages:
            if page.confidence < threshold:
                page.flags.append(Anomaly.low_confidence)
    return scans


def flag_dimensions_anomalies(scans: list[Scan]) -> list[Scan]:
    """Flags pages whose aspect ratio or area is a robust outlier for this title.

    Replaces the previous hard 5% threshold with a median +- k*MAD test (adapts
    to the natural spread of the document) and adds an absolute minimum-area
    floor so spurious tiny detections are caught even when many boxes are small.
    Structural scans (cover/wrapper/back cover) are excluded.
    """
    structural = _structural_indices(len(scans))

    ratios, areas = [], []
    for i, scan in enumerate(scans):
        if i in structural:
            continue
        for page in scan.predicted_pages:
            if page.height > 0:
                ratios.append(page.width / page.height)
            areas.append(page.width * page.height)

    if not ratios or not areas:
        return scans

    ratio_med, ratio_mad = _median_mad(ratios)
    area_med, area_mad = _median_mad(areas)

    for i, scan in enumerate(scans):
        if i in structural:
            continue
        for page in scan.predicted_pages:
            area = page.width * page.height
            ratio = page.width / page.height if page.height > 0 else 0.0

            ratio_out = ratio_mad > 0 and abs(ratio - ratio_med) > DIM_MAD_K * ratio_mad
            area_out = area_mad > 0 and abs(area - area_med) > DIM_MAD_K * area_mad
            too_small = area < DIM_MIN_AREA

            if ratio_out or area_out or too_small:
                page.flags.append(Anomaly.dimensions)
    return scans


def flag_prediction_overlaps(scans: list[Scan]) -> list[Scan]:
    """Flags pages that overlap another page by more than ``OVERLAP_MIN_RATIO``.

    Uses intersection-over-min-area (rather than IoU) so duplicate/nested
    detections are caught while normal adjacent spread pages that merely touch
    are not.
    """
    for scan in scans:
        pages = scan.predicted_pages
        if len(pages) < 2:
            continue
        for i in range(len(pages)):
            for j in range(i + 1, len(pages)):
                if _intersection_over_min(pages[i], pages[j]) > OVERLAP_MIN_RATIO:
                    pages[i].flags.append(Anomaly.prediction_overlap)
                    pages[j].flags.append(Anomaly.prediction_overlap)
    return scans


def flag_missing_pages(scans: list[Scan]) -> list[Scan]:
    """Flags a scan that likely has an undetected second page.

    Only when the title is dominated by two-page spreads, and only for a single
    detected page that is narrow enough to leave room for a second one (so a
    full-width single page like a cover is not flagged). Structural scans are
    excluded.
    """
    n = len(scans)
    if n == 0:
        return scans

    # Only meaningful when most scans are double-page spreads.
    multi = sum(1 for s in scans if len(s.predicted_pages) >= 2)
    if multi / n < 0.5:
        return scans

    structural = _structural_indices(n)
    for i, scan in enumerate(scans):
        if i in structural or len(scan.predicted_pages) != 1:
            continue
        page = scan.predicted_pages[0]
        # Narrow single page => there is empty room beside it => likely missing.
        if page.width <= MISSING_MAX_WIDTH:
            page.flags.append(Anomaly.page_count_mismatch)
    return scans


def flag_bad_split(scans: list[Scan]) -> list[Scan]:
    """Flags two-page spreads that were likely split incorrectly.

    Looks at what ``odd_dimensions`` cannot: the relationship between the two
    boxes - large width asymmetry or an off-center seam between the pages.
    """
    for scan in scans:
        if len(scan.predicted_pages) != 2:
            continue
        left, right = sorted(scan.predicted_pages, key=lambda p: p.xc)

        widest = max(left.width, right.width)
        asymmetry = abs(left.width - right.width) / widest if widest > 0 else 0.0

        seam = ((left.xc + left.width / 2) + (right.xc - right.width / 2)) / 2
        seam_off = abs(seam - 0.5)

        if asymmetry > SPLIT_ASYMMETRY or seam_off > SPLIT_SEAM_OFFSET:
            left.flags.append(Anomaly.split)
            right.flags.append(Anomaly.split)
    return scans


def flag_prediction_errors(scans: list[Scan]) -> list[Scan]:
    """Adds a full-frame flagged page to any scan where nothing was detected.

    Run last so the synthetic page is not picked up by the other checks.
    """
    for scan in scans:
        if len(scan.predicted_pages) == 0:
            scan.predicted_pages.append(
                Page(
                    xc=0.5,
                    yc=0.5,
                    width=1.0,
                    height=1.0,
                    confidence=0.0,
                    flags=[Anomaly.prediction_error],
                )
            )
    return scans
