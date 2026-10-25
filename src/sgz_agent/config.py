import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    executable: Path
    data_dir: Path
    profile_id: str = "local"
    identity: dict = field(default_factory=dict)
    ui: dict = field(default_factory=dict)
    ocr: str = "paddle"


def load_config(path: str | Path) -> AgentConfig:
    source = Path(path).resolve()
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    executable = Path(data["executable"])
    if not executable.is_absolute() or executable.suffix.lower() != ".exe":
        raise ValueError("executable must be an absolute .exe path")
    data_dir = Path(data.get("data_dir", "../.state"))
    if not data_dir.is_absolute():
        data_dir = source.parent / data_dir
    return AgentConfig(executable, data_dir.resolve(), data.get("profile_id", "local"),
                       data.get("identity", {}), data.get("ui", {}), data.get("ocr", "paddle"))
