"""Prétraitement d'image (PLAN.md §7) : redressement, normalisation du fond, CLAHE, débruitage, netteté.

Le redressement est appliqué à l'image couleur d'origine (qui sert aux découpes) *avant* le prétraitement :
les deux images restent alignées pixel à pixel, donc les boîtes détectées sur l'image prétraitée sont
valables sur l'image d'origine.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

PROFILES = ("leger", "delave", "tres_delave")


@dataclass(frozen=True)
class Params:
    bg_kernel_frac: float  # taille du flou de fond, en fraction du côté long
    clahe_clip: float
    denoise: int  # force du débruitage (0 = aucun)
    sharpen: float
    binarize: bool


PARAMS = {
    "leger": Params(0.05, 1.5, 0, 0.4, False),
    "delave": Params(0.04, 3.0, 5, 0.8, False),
    "tres_delave": Params(0.035, 4.0, 9, 1.0, True),
}


def estimate_skew(gray: np.ndarray, max_angle: float = 5.0, step: float = 0.25) -> float:
    """Angle (degrés) à appliquer pour redresser, par maximisation de la variance du profil de projection."""
    h, w = gray.shape
    scale = 800 / max(h, w)
    small = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA) if scale < 1 else gray
    _, bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if np.count_nonzero(bw) < 50:
        return 0.0
    hh, ww = bw.shape
    center = (ww / 2, hh / 2)
    best_angle, best_score = 0.0, -1.0
    for a in np.arange(-max_angle, max_angle + 1e-6, step):
        m = cv2.getRotationMatrix2D(center, float(a), 1.0)
        rot = cv2.warpAffine(bw, m, (ww, hh), flags=cv2.INTER_NEAREST)
        score = float(np.var(rot.sum(axis=1)))
        if score > best_score:
            best_angle, best_score = float(a), score
    return best_angle


def rotate(img: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.15:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    fill = (255, 255, 255) if img.ndim == 3 else 255
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=fill)


def measure_contrast(gray: np.ndarray) -> float:
    """Écart entre l'encre et le papier (p2 vs p98 après suppression du fond) : élevé = document net."""
    flat = remove_background(gray, 0.05)
    p2, p98 = np.percentile(flat, [1, 98])
    return float(p98 - p2)


def choose_profile(gray: np.ndarray) -> str:
    c = measure_contrast(gray)
    if c >= 200:
        return "leger"
    if c >= 140:
        return "delave"
    return "tres_delave"


def remove_background(gray: np.ndarray, kernel_frac: float) -> np.ndarray:
    """Division par le fond flouté : supprime l'éclairage inégal et le jaunissement du papier."""
    k = int(max(gray.shape) * kernel_frac) | 1
    bg = cv2.medianBlur(cv2.resize(gray, (0, 0), fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA), (k // 4) | 1)
    bg = cv2.resize(bg, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    out = cv2.divide(gray, np.maximum(bg, 1), scale=255)
    return out


def enhance(gray: np.ndarray, profile: str) -> np.ndarray:
    p = PARAMS[profile]
    img = remove_background(gray, p.bg_kernel_frac)
    clahe = cv2.createCLAHE(clipLimit=p.clahe_clip, tileGridSize=(8, 8))
    img = clahe.apply(img)
    if p.denoise:
        img = cv2.fastNlMeansDenoising(img, None, h=p.denoise, templateWindowSize=7, searchWindowSize=21)
    if p.sharpen:
        blur = cv2.GaussianBlur(img, (0, 0), 1.2)
        img = cv2.addWeighted(img, 1 + p.sharpen, blur, -p.sharpen, 0)
    if p.binarize:
        img = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)
    return img


def process_page(bgr: np.ndarray, profile: str = "auto") -> tuple[np.ndarray, np.ndarray, str, float]:
    """Retourne (image couleur redressée, image prétraitée RGB, profil utilisé, angle appliqué)."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    angle = estimate_skew(gray)
    bgr = rotate(bgr, angle)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    used = choose_profile(gray) if profile == "auto" else profile
    if used not in PARAMS:
        raise ValueError(f"profil inconnu : {used}")
    out = enhance(gray, used)
    return bgr, cv2.cvtColor(out, cv2.COLOR_GRAY2RGB), used, angle
