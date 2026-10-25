import contextlib
import os
from pathlib import Path
import sys
import threading

from .models import TextBox


class Vision:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self._engine = None
        self._lock = threading.Lock()

    def read(self, frame):
        import numpy as np
        with self._lock, contextlib.redirect_stdout(sys.stderr):
            if self._engine is None:
                os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(self.cache_dir))
                from paddleocr import PaddleOCR
                self._engine = PaddleOCR(
                    text_detection_model_name="PP-OCRv5_mobile_det",
                    text_recognition_model_name="PP-OCRv5_mobile_rec",
                    use_doc_orientation_classify=False, use_doc_unwarping=False,
                    use_textline_orientation=False, device="cpu", cpu_threads=4,
                    enable_mkldnn=False,
                )
            width, height = frame.image.size
            results = list(self._engine.predict(np.asarray(frame.image)[:, :, ::-1].copy()))
            boxes = []
            for result in results:
                for text, score, poly in zip(result["rec_texts"], result["rec_scores"], result["rec_polys"]):
                    points = np.asarray(poly)
                    box = (float(points[:, 0].min()/width), float(points[:, 1].min()/height),
                           float(points[:, 0].max()/width), float(points[:, 1].max()/height))
                    boxes.append(TextBox(str(text), float(score), box))
            return boxes
