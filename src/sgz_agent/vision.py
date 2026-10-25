import contextlib
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

from .models import TextBox


class Vision:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self._engine = None
        self._lock = threading.Lock()

    def read_regions(self, frame, crops):
        width, height = frame.image.size
        output = []
        for crop in crops:
            left, top, right, bottom = crop['box']
            scale = crop.get('scale', 2)
            if not (0 <= left < right <= width and 0 <= top < bottom <= height and 1 <= scale <= 4):
                raise ValueError('invalid_ocr_crop')
            patch = frame.image.crop((left, top, right, bottom))
            patch = patch.resize((int(patch.width*scale), int(patch.height*scale)))
            for item in self.read(SimpleNamespace(image=patch)):
                x0,y0,x1,y1 = item.box
                output.append(TextBox(item.text, item.score,
                    ((left+x0*(right-left))/width, (top+y0*(bottom-top))/height,
                     (left+x1*(right-left))/width, (top+y1*(bottom-top))/height)))
        return output

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
