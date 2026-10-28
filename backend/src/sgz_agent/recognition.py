from dataclasses import dataclass, field
import re
import unicodedata

from .models import TextBox


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def coordinate_readings(text):
    value = normalize(text)
    # Read full numeric tokens before validation; never truncate fractions or long integers.
    number = r"([+-]?\d[\d.eE+-]*)"
    patterns = [rf"(?:坐标|座标)[:：]?{number}[,，]{number}",
                rf"\({number}[,，]{number}\)",
                rf"[Xx][:：]{number}[,，]?[Yy][:：]{number}"]
    readings = set()
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            if all(re.fullmatch(r"[0-9]{1,5}", token) for token in match.groups()):
                readings.add((int(match[1]), int(match[2])))
            else:
                readings.add(None)  # Preserve invalid evidence instead of silently ignoring it.
    return readings


def parse_coordinate(text):
    readings = coordinate_readings(text)
    return next(iter(readings)) if len(readings) == 1 else None


@dataclass(frozen=True)
class ScreenState:
    kind: str
    texts: tuple[TextBox, ...] = ()
    coordinate: tuple[int, int] | None = None
    identity_matched: bool = False
    fields: dict = field(default_factory=dict)


class Recognizer:
    def __init__(self, profile, identity):
        self.profile, self.identity = profile, identity

    def classify(self, texts):
        good = [t for t in texts if t.score >= .85]
        joined = "\n".join(normalize(t.text) for t in good)
        points = {p for t in good for p in coordinate_readings(t.text)}
        coordinate = next(iter(points)) if len(points) == 1 else None
        values = [normalize(str(v)) for v in self.identity.values() if str(v).strip()]
        identity = bool(values) and all(v in joined.splitlines() for v in values)
        kind = "unknown"
        if re.search(r"二维码.*(失效|过期)|点击.*刷新.*二维码", joined):
            kind = "qr_expired"
        elif "扫一扫" in joined and ("扫码指引" in joined or "请使用" in joined):
            kind = "qr_ready"
        elif any(word in joined for word in ["加载中", "连接服务器", "正在更新"]):
            kind = "loading"
        else:
            matches = []
            for name, anchors in self.profile.get("anchors", {}).items():
                if anchors and all(self._matches(anchor, good) for anchor in anchors):
                    matches.append(name)
            if len(matches) == 1:
                kind = matches[0]
        return ScreenState(kind, tuple(texts), coordinate, identity)

    @staticmethod
    def _matches(anchor, texts):
        if isinstance(anchor, str):
            return any(normalize(anchor) in normalize(t.text) for t in texts)
        pattern = anchor["text"]
        region = anchor.get("region", [0, 0, 1, 1])
        for t in texts:
            cx, cy = (t.box[0] + t.box[2])/2, (t.box[1] + t.box[3])/2
            if region[0] <= cx <= region[2] and region[1] <= cy <= region[3]:
                if re.search(pattern, normalize(t.text)):
                    return True
        return False
