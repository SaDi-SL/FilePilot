"""Read-only local model inventory; never loads models or runs inference."""
from dataclasses import dataclass
from urllib import request

from app.ai_service import OLLAMA_BASE_URL, _request_json
from app.embedding_service import DEFAULT_OLLAMA_EMBEDDING_MODEL


@dataclass(frozen=True)
class LocalAIReadiness:
    reachable: bool
    models: tuple[str, ...]
    answer_model: str
    answer_installed: bool
    embedding_model: str
    embedding_installed: bool
    error: str = ""

    @property
    def installed(self) -> bool:
        return self.reachable and self.answer_installed and self.embedding_installed


def model_is_installed(model: str, models: tuple[str, ...]) -> bool:
    # Only the implicit :latest alias is interchangeable; other tags are exact.
    def canonical(name: str) -> str:
        name = name.strip().casefold()
        return name if ":" in name else name + ":latest"
    return bool(model.strip()) and canonical(model) in {canonical(name) for name in models}


def check_local_ai(answer_model: str) -> LocalAIReadiness:
    embedding = DEFAULT_OLLAMA_EMBEDDING_MODEL
    try:
        data = _request_json(request.Request(f"{OLLAMA_BASE_URL}/api/tags"),
                             timeout=3.0, provider_name="Local Ollama")
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            raise ValueError("Invalid model inventory")
        names = []
        for item in data["models"]:
            if not isinstance(item, dict):
                raise ValueError("Invalid model entry")
            name = item.get("name") or item.get("model")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Invalid model name")
            names.append(name)
        models = tuple(sorted(set(names), key=str.casefold))
    except Exception:
        return LocalAIReadiness(False, (), answer_model, False, embedding, False,
                                "Could not read Ollama models. Open Ollama on this computer and try again.")
    return LocalAIReadiness(True, models, answer_model,
                            model_is_installed(answer_model, models), embedding,
                            model_is_installed(embedding, models))


def prepare_local_ai(answer_model: str) -> LocalAIReadiness:
    """Start an installed Windows Ollama server on demand; never install models."""
    import os
    import shutil
    import subprocess
    import sys
    import time
    from pathlib import Path

    result = check_local_ai(answer_model)
    if result.reachable or sys.platform != "win32":
        return result
    executable = shutil.which("ollama.exe")
    if not executable:
        local = os.environ.get("LOCALAPPDATA")
        candidate = Path(local) / "Programs" / "Ollama" / "ollama.exe" if local else None
        if candidate is not None and candidate.is_file():
            executable = str(candidate)
    if not executable:
        return LocalAIReadiness(False, (), answer_model, False, result.embedding_model, False,
            "Ollama was not found. Install Ollama, then select Prepare local AI. Your files stay on this computer.")
    try:
        environment = dict(os.environ)
        environment["OLLAMA_HOST"] = "127.0.0.1:11434"
        subprocess.Popen([executable, "serve"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        return LocalAIReadiness(False, (), answer_model, False, result.embedding_model, False,
            "Windows could not start Ollama. Open Ollama from Start, then try again.")
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        time.sleep(0.25)
        result = check_local_ai(answer_model)
        if result.reachable:
            return result
    return result
