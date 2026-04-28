"""`.doc` → `.docx` 转换。

python-docx 不能直接读老 Word 二进制 `.doc`；统一先用 LibreOffice headless 转 docx。
失败抛 `DocConversionError`，调用方决定回退（如手动转换或 antiword）。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path


log = logging.getLogger(__name__)


class DocConversionError(RuntimeError):
    """`.doc` 转换失败。"""


def _find_soffice() -> str:
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    raise DocConversionError(
        "未找到 LibreOffice。请安装：\n"
        "  Debian/Ubuntu: sudo apt install libreoffice\n"
        "  CentOS/RHEL : sudo yum install libreoffice\n"
        "  macOS       : brew install --cask libreoffice"
    )


def convert_doc_to_docx(
    src: str | Path,
    out_dir: str | Path | None = None,
    *,
    timeout: float = 60.0,
) -> Path:
    """把 `.doc` 转成 `.docx`，返回新文件路径。

    - 输入已经是 `.docx`/`.docm`：直接复制到 `out_dir` 后返回。
    - 输入是 `.doc`：调用 LibreOffice headless 转换。
    - 失败时抛 `DocConversionError`，调用方应有降级策略。
    """
    src_path = Path(src).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src_path}")

    out_path = Path(out_dir).resolve() if out_dir else src_path.parent
    out_path.mkdir(parents=True, exist_ok=True)

    suffix = src_path.suffix.lower()
    if suffix in (".docx", ".docm"):
        dst = out_path / src_path.name
        if dst.resolve() != src_path:
            shutil.copy2(src_path, dst)
        return dst

    if suffix != ".doc":
        raise DocConversionError(f"不支持的扩展名: {suffix}")

    soffice = _find_soffice()
    cmd = [
        soffice,
        "--headless",
        "--convert-to",
        "docx",
        "--outdir",
        str(out_path),
        str(src_path),
    ]
    log.debug("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise DocConversionError(f"LibreOffice 转换超时 (>{timeout}s)") from e

    if result.returncode != 0:
        raise DocConversionError(
            f"LibreOffice 退出码 {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    converted = out_path / (src_path.stem + ".docx")
    if not converted.exists():
        raise DocConversionError(
            f"LibreOffice 未生成预期文件: {converted}\nstdout: {result.stdout}"
        )
    return converted
