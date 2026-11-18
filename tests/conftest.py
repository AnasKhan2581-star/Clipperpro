import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="clipper-test-"))
shutil.copytree(ROOT / "config", _TMP / "config")
os.environ["CLIPPER_CONFIG_DIR"] = str(_TMP / "config")
os.environ["CLIPPER_DATA_DIR"] = str(_TMP / "data")


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """90s 16:9 video: quiet noise with a loud 'hype' burst at 40-46s."""
    out = tmp_path_factory.mktemp("media") / "vod.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=90",
        "-f", "lavfi", "-i", "anoisesrc=d=90:c=pink:a=0.02",
        "-f", "lavfi", "-i", "sine=f=440:d=90",
        "-filter_complex", "[2:a]volume='if(between(t,40,46),0.9,0)':eval=frame[b];[1:a][b]amix=inputs=2:normalize=0[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out),
    ], check=True)
    return out
