"""Optional Windows Authenticode signing using an existing certificate store identity."""
import os
import re
import shutil
import subprocess
from pathlib import Path


def signing_command(path, *, verify=False):
    if os.name != "nt":
        raise ValueError("Release signing requires Windows and SignTool.")
    tool = os.environ.get("FILEPILOT_SIGNTOOL") or shutil.which("signtool")
    if not tool or not Path(tool).is_file():
        raise ValueError("SignTool was not found. Set FILEPILOT_SIGNTOOL to the Windows SDK signtool.exe path.")
    if verify:
        return [tool, "verify", "/pa", "/all", str(path)]
    thumbprint = re.sub(r"\s", "", os.environ.get("FILEPILOT_CERT_THUMBPRINT", ""))
    if not re.fullmatch(r"[0-9a-fA-F]{40}", thumbprint):
        raise ValueError("Set FILEPILOT_CERT_THUMBPRINT to an existing code-signing certificate in the current user's Windows certificate store.")
    return [tool, "sign", "/sha1", thumbprint, "/s", "My", "/fd", "SHA256",
            "/tr", "http://timestamp.digicert.com", "/td", "SHA256", str(path)]


def verify_binary(path):
    subprocess.run(signing_command(path, verify=True), check=True, timeout=60)


def sign_binary(path):
    command = signing_command(path)
    subprocess.run(command, check=True, timeout=180)
    verify_binary(path)
