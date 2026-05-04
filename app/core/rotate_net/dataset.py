import random
from PIL import Image
from torchvision import transforms
import numpy as np
from torch.utils.data import Dataset

import cv2
import torch


class PageAngleDataset(Dataset):
    """Dataset for page image rotation angle prediction."""

    def __init__(
        self,
        image_paths: list[str],
        image_bboxes: list[tuple[float, float, float, float]],
        image_size: int = 640,
        is_train: bool = True,
        angle_max: float = 10.0,
        aug_rotate_prob: float = 0.9,
        use_canny: bool = False,
    ):
        self.image_paths = image_paths
        self.image_bboxes = image_bboxes
        self.image_size = image_size
        self.is_train = is_train
        self.angle_max = angle_max
        self.aug_rotate_prob = aug_rotate_prob
        self.use_canny = use_canny

        self.normalize_tf = transforms.Compose(
            [
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.25, 0.25, 0.25]),
            ]
        )

    def __len__(self) -> int:
        return len(self.image_paths)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        # Load image
        img_path = self.image_paths[idx]
        img = cv2.imread(img_path)

        angle = 0.0
        # Load crop coordinates
        img_h, img_w, _ = img.shape
        xc, yc, w, h = self._denormalize_bbox(self.image_bboxes[idx], img_w, img_h)

        try:
            # Augment images: rotation, translation jitter
            if self.is_train:
                if random.random() < self.aug_rotate_prob:
                    xc, yc, w, h = self._add_jitter(
                        xc, yc, w, h, img_w, img_h, jitter_percent=0.05
                    )
                    angle = random.uniform(-self.angle_max, self.angle_max)
                    img = self._rotate_around_center(img, angle, xc, yc)
            else:
                w, h = int(w * 1.05), int(h * 1.05)  # Add 5% padding to bbox

            # Convert to bounding box and clamp
            x1, y1, x2, y2 = self._cxywh_to_xyxy(xc, yc, w, h)
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(img_w, x2)
            y2 = min(img_h, y2)

            # Crop
            if x1 < x2 and y1 < y2:
                img = img[y1:y2, x1:x2]

            # Binarize images
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            # small opening to remove isolated noise
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)
            img = cv2.cvtColor(bw, cv2.COLOR_GRAY2RGB)

            if self.use_canny:
                img = cv2.Canny(img, 100, 200)
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)

            # Resize with padding
            img = self._resize_letterbox_pad(img, self.image_size)

            # Normalize and convert to tensor
            img_pil = Image.fromarray(img)
            img_t = self.normalize_tf(img_pil)
        except Exception as e:
            print(
                f"Error processing image {img_path}: {e}, {xc}, {yc}, {w}, {h}, {angle}"
            )
            raise e
        return img_t, torch.tensor([angle], dtype=torch.float32), angle

    def _denormalize_bbox(
        self, bbox: tuple[float, float, float, float], img_w: int, img_h: int
    ) -> tuple[int, int, int, int]:
        return (
            int(bbox[0] * img_w),
            int(bbox[1] * img_h),
            int(bbox[2] * img_w),
            int(bbox[3] * img_h),
        )

    def _cxywh_to_xyxy(
        self, xc: int, yc: int, w: int, h: int
    ) -> tuple[int, int, int, int]:
        x1 = int(xc - w / 2)
        y1 = int(yc - h / 2)
        x2 = int(xc + w / 2)
        y2 = int(yc + h / 2)
        return x1, y1, x2, y2

    def _rotate_around_center(
        self, img: np.ndarray, angle: float, xc: int, yc: int
    ) -> np.ndarray:
        # Compute rotation matrix
        M = cv2.getRotationMatrix2D((xc, yc), angle, 1.0)
        # Perform affine warp (rotation)
        rotated = cv2.warpAffine(
            img,
            M,
            (img.shape[1], img.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        return rotated

    def _add_jitter(
        self,
        xc: int,
        yc: int,
        w: int,
        h: int,
        img_w: int,
        img_h: int,
        jitter_percent: float = 0.04,
    ) -> tuple[int, int, int, int]:
        """Add random jitter to the bounding box coordinates."""
        xc += random.normalvariate(0, jitter_percent * img_w)
        yc += random.normalvariate(0, jitter_percent * img_h)

        w += random.uniform(-2 * jitter_percent * img_w, 2 * jitter_percent * img_w)
        h += random.uniform(-2 * jitter_percent * img_h, 2 * jitter_percent * img_h)

        # Ensure bbox is within image boundaries
        if xc + w / 2 > img_w or xc - w / 2 < 0:
            w = min(w, 2 * min(xc, img_w - xc))
        if yc + h / 2 > img_h or yc - h / 2 < 0:
            h = min(h, 2 * min(yc, img_h - yc))

        return xc, yc, w, h

    def _resize_letterbox_pad(self, img: np.ndarray, size: int) -> np.ndarray:
        # Resize image with letterbox padding to keep aspect ratio
        # +---------------------------+
        # |         gray pad          |
        # |+-------------------------+|
        # ||        resized          ||
        # ||        640×480          ||
        # |+-------------------------+|
        # |         gray pad          |
        # +---------------------------+
        h, w = img.shape[:2]
        scale = size / max(h, w)

        nh, nw = int(h * scale), int(w * scale)
        nh, nw = max(nh, 1), max(nw, 1)
        img_resized = cv2.resize(img, (nw, nh))
        vertical_pad = (size - nh) // 2
        horizontal_pad = (size - nw) // 2
        img_padded = cv2.copyMakeBorder(
            img_resized,
            vertical_pad,
            vertical_pad,
            horizontal_pad,
            horizontal_pad,
            cv2.BORDER_CONSTANT,
            value=(114, 114, 114),
        )
        return cv2.resize(img_padded, (size, size))
