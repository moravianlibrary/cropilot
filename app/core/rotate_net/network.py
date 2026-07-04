import os

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torchvision import models
from tqdm import tqdm


def resolve_device() -> torch.device:
    """Device for rotation inference.

    Honors the ``ROTATION_DEVICE`` env var (e.g. ``cpu``, ``cuda``, ``cuda:0``);
    otherwise uses CUDA when available and falls back to CPU (production).
    """
    override = os.getenv("ROTATION_DEVICE")
    if override:
        return torch.device(override)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


class DegreeHead(nn.Module):
    """Features -> degrees in [-angle_max, angle_max] via tanh bound."""

    def __init__(self, in_features: int, angle_max: float = 10.0):
        super().__init__()
        self.angle_max = float(angle_max)
        self.net = nn.Sequential(
            nn.Linear(in_features, 256),
            nn.ReLU(inplace=True),
            nn.Linear(256, 1),
        )

    def forward(self, x):
        raw = self.net(x)
        return torch.tanh(raw) * self.angle_max


class AngleDegModel(nn.Module):
    """ResNet18 backbone + degree head."""

    def __init__(self, model=None, angle_max: float = 10.0, device=None):
        super().__init__()
        # No ImageNet weights: a checkpoint is loaded over them anyway, so
        # downloading/loading pretrained weights would just waste time + memory.
        base = models.resnet18(weights=None)
        in_feats = base.fc.in_features
        base.fc = nn.Identity()

        self.backbone = base
        self.head = DegreeHead(in_feats, angle_max)

        if model is not None:
            # These checkpoints are produced by our training pipeline and store
            # a dict containing the model state. PyTorch 2.6 defaults
            # weights_only=True, which rejects this legacy checkpoint shape.
            ckpt = torch.load(model, map_location="cpu", weights_only=False)
            self.load_state_dict(ckpt["model"])

        # Move to the target device once and switch to eval for inference.
        self.to(device if device is not None else resolve_device())
        self.eval()

    @property
    def device(self):
        return next(self.parameters()).device

    def forward(self, x):
        """Forward pass."""
        feats = self.backbone(x)
        return self.head(feats)  # (B,1)


def predict_angles(model, loader: DataLoader) -> np.ndarray:
    """Predict angles for all images in data loader."""
    device = model.device
    model.eval()
    preds = []
    with torch.inference_mode():
        for imgs, _, _ in tqdm(loader, desc="Predict rotation", leave=False):
            imgs = imgs.to(device, non_blocking=True)
            outputs = model(imgs)
            preds.append(outputs.float().cpu().numpy().reshape(-1))
    if not preds:
        return np.array([])
    return np.concatenate(preds)
