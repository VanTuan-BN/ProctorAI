import os
import sys
from pathlib import Path


def configure_windows_dll_paths():
    if sys.platform != "win32":
        return []

    executable_path = Path(sys.executable).resolve()
    env_root = executable_path.parent
    if executable_path.parent.name.lower() == "scripts":
        env_root = executable_path.parent.parent

    candidate_dirs = [
        env_root / "Library" / "bin",
        env_root / "DLLs",
        env_root / "Lib" / "site-packages" / "torch" / "lib",
        env_root / "Lib" / "site-packages" / "cv2",
    ]

    added_dirs = []
    current_path = os.environ.get("PATH", "")
    current_parts = current_path.split(os.pathsep) if current_path else []

    for directory in candidate_dirs:
        if not directory.exists():
            continue

        dir_str = str(directory)
        if dir_str not in current_parts:
            current_parts.insert(0, dir_str)

        try:
            os.add_dll_directory(dir_str)
        except (AttributeError, FileNotFoundError, OSError):
            pass

        added_dirs.append(dir_str)

    os.environ["PATH"] = os.pathsep.join(current_parts)
    return added_dirs