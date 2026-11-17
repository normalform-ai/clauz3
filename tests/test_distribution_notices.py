"""Check the legal text and the actual wheel built from its source distribution."""

import hashlib
import subprocess
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR_LICENSE = "src/clauz3/_vendor/deal_solver/LICENSE"


def test_legal_wording() -> None:
    # Verified against both authoritative MIME alternatives, with HTML bold
    # punctuation rendered as punctuation. Only whitespace wrapping may vary.
    text = " ".join((ROOT / "license.txt").read_text().split())
    assert hashlib.sha256(text.encode()).hexdigest() == (
        "e1bf6aebba092308d1b2299e8835343d2ab13ee62c91e46d74cb4aacdde7070c"
    )


def test_distribution_notices(tmp_path: Path) -> None:
    # uv's default builds the sdist first, then builds the wheel from that sdist.
    subprocess.run(
        ["uv", "build", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    expected = {
        "license.txt": (ROOT / "license.txt").read_bytes(),
        VENDOR_LICENSE: (ROOT / VENDOR_LICENSE).read_bytes(),
    }
    with tarfile.open(next(tmp_path.glob("*.tar.gz"))) as archive:
        prefix = archive.getnames()[0].split("/")[0]
        for name, content in expected.items():
            member = archive.extractfile(f"{prefix}/{name}")
            assert member is not None
            assert member.read() == content
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as wheel:
        metadata_path = next(x for x in wheel.namelist() if x.endswith("/METADATA"))
        metadata = Parser().parsestr(wheel.read(metadata_path).decode())
        assert metadata["License-Expression"] == "LicenseRef-ClauZ3 AND MIT"
        assert set(metadata.get_all("License-File", [])) == set(expected)
        dist_info = metadata_path.rsplit("/", 1)[0]
        for name, content in expected.items():
            assert wheel.read(f"{dist_info}/licenses/{name}") == content
        assert (
            wheel.read("clauz3/_vendor/deal_solver/LICENSE") == expected[VENDOR_LICENSE]
        )
        assert "clauz3/stdlib/google_workspace/LICENSE" not in wheel.namelist()
