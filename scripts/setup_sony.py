"""Fetch the optional upstream Sony source and checkpoint at fixed revisions."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / ".model"
SONY_SOURCE = "https://raw.githubusercontent.com/SonyResearch/Fx-Encoder_PlusPlus/7b1e1f7efaa8a7c212ae4d62c01a5bac5f61c47b"
SOURCE_SHA = "ab42dd619ef8c3f387923a9a6527f033fca10e76b84411b706941016ea9cef47"
WEIGHTS = "https://huggingface.co/yytung/fxencoder-plusplus/resolve/d6ed786a5fa20126402121ba8e28e1a6427eea52/fxenc_plusplus_default.pt"
WEIGHTS_SHA = "9291f51c4beb6a3bfd1c50aafeab3e3cbcc5d20f9a87234f2205f8156177c14c"


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def fetch(url, destination, expected=None):
    subprocess.run(["curl", "--fail", "--location", "--retry", "2", "--output", destination, url], check=True)
    if expected and digest(destination) != expected:
        raise ValueError(f"SHA-256 mismatch for {destination.name}.")


def main():
    MODEL.mkdir(exist_ok=True)
    custom = os.environ.get("MIX_LISTENER_CHECKPOINT")
    checkpoint = Path(custom).expanduser() if custom else MODEL / "fx-encoder.pt"
    with TemporaryDirectory(dir=MODEL) as temporary:
        staging = Path(temporary)
        if not checkpoint.exists() and not custom:
            print("Downloading the 1.6 GB Fx-Encoder++ checkpoint…", flush=True)
            download = staging / checkpoint.name
            fetch(WEIGHTS, download, WEIGHTS_SHA)
            download.replace(checkpoint)
        elif not checkpoint.is_file():
            raise ValueError(f"Checkpoint not found: {checkpoint}")
        elif digest(checkpoint) != WEIGHTS_SHA:
            raise ValueError(f"Checkpoint SHA-256 mismatch: {checkpoint}")

        source = staging / "fx_mixture_model.py"
        fetch(f"{SONY_SOURCE}/fxencoder_plusplus/model.py", source, SOURCE_SHA)
        source_text = source.read_text(encoding="utf-8")
        # Import LAION only inside its optional encoder branches. This workflow uses
        # the stereo mixture encoder and does not instantiate either branch.
        source_text = source_text.replace("import laion_clap\n", "", 1)
        source_text = source_text.replace("        self.encoder = laion_clap.CLAP_Module",
                                          "        import laion_clap\n        self.encoder = laion_clap.CLAP_Module")
        source.write_text("# Adapted from SonyResearch/Fx-Encoder_PlusPlus (CC BY-NC 4.0).\n" + source_text,
                          encoding="utf-8")
        license_file = staging / "SONY-LICENSE"
        fetch(f"{SONY_SOURCE}/LICENSE", license_file)
        license_file.replace(MODEL / license_file.name)
        source.replace(MODEL / source.name)
    print("Sony Fx-Encoder++ is ready for local comparisons.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Sony setup failed: {error}")
