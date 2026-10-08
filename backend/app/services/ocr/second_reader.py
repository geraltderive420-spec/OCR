"""Second lecteur des blocs douteux : PaddleOCR-VL 1.5 (0,9 Md paramètres, Apache-2.0, 109 langues dont l'arabe).

Servi par un second `llama-server` (GGUF officiel, cache Hugging Face local), sur son propre port. Les deux lectures
d'un bloc sont confrontées : si elles concordent, le bloc est sûr ; sinon on garde la meilleure et le bloc est
signalé « à vérifier ». Sur une carte de 4 Go, ce serveur n'est démarré qu'après l'arrêt de celui de Surya.
"""
from __future__ import annotations

import base64
import io
import logging
import math

import httpx
from PIL import Image

from app.services.gpu import LlamaServer, LlamaServerError
from app.services.ocr_config import OcrConfig

log = logging.getLogger("ocr.second")
REPO = "PaddlePaddle/PaddleOCR-VL-1.5-GGUF"
FILES = ("PaddleOCR-VL-1.5.gguf", "PaddleOCR-VL-1.5-mmproj.gguf")
ALIAS = "paddleocr-vl-1.5"


def _gguf_paths() -> tuple[str, str]:
    from huggingface_hub import hf_hub_download

    try:
        return tuple(hf_hub_download(REPO, f, local_files_only=True) for f in FILES)  # type: ignore[return-value]
    except Exception as e:  # noqa: BLE001
        raise LlamaServerError("Fichiers GGUF de PaddleOCR-VL absents du cache local.", permanent=True) from e


def available() -> bool:
    try:
        _gguf_paths()
        return True
    except LlamaServerError:
        return False


class SecondReader:
    def __init__(self, port: int | None = None, config: OcrConfig | None = None):
        config = config or OcrConfig.from_env()
        self.server = LlamaServer(port or config.llama_port + 1, model_files=_gguf_paths, alias=ALIAS,
                                  ctx_size=8192, log_name="llama-server-second", config=config)

    def start(self) -> None:
        self.server.start()

    def stop(self) -> None:
        self.server.stop()

    def read(self, crop: Image.Image, max_tokens: int = 1024) -> tuple[str, float | None]:
        """Texte brut d'une découpe et probabilité moyenne des tokens (None si indisponible)."""
        buf = io.BytesIO()
        crop.convert("RGB").save(buf, format="PNG")
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        body = {
            "model": ALIAS, "temperature": 0, "max_tokens": max_tokens, "logprobs": True,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                      {"type": "text", "text": "OCR:"}]}],
        }
        r = httpx.post(f"{self.server.base_url}/chat/completions", json=body, timeout=300)
        r.raise_for_status()
        choice = r.json()["choices"][0]
        text = (choice["message"].get("content") or "").strip()
        lps = [t.get("logprob") for t in ((choice.get("logprobs") or {}).get("content") or []) if t.get("logprob") is not None]
        return text, (sum(math.exp(lp) for lp in lps) / len(lps)) if lps else None
