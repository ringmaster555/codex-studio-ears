"""Capture a stereo passage and compare it locally with a reference."""
import argparse
from datetime import datetime
from functools import lru_cache
import importlib.util
from math import gcd
import os
from pathlib import Path
import shlex
import subprocess
import sys
from uuid import uuid4

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / ".model"
DEFAULT_CHECKPOINT = MODEL_DIR / "fx-encoder.pt"
MODEL_RATE = 44_100
WINDOW_SECONDS = 2
METRICS = (
    "Level (RMS, dBFS)",
    "Peak above RMS (crest factor)",
    "Stereo side relative to mid",
    "Right relative to left",
    "Low end (40–120 Hz share)",
    "Presence (3–10 kHz share)",
)


def new_session():
    destination = Path(os.environ.get("MIX_LISTENER_RESULTS", ROOT / "Sessions")).expanduser()
    folder = destination.resolve() / f"{datetime.now():%Y-%m-%d %H-%M-%S} {uuid4().hex[:8]}"
    folder.mkdir(parents=True)
    return folder


def capture_tool():
    if sys.platform != "darwin":
        raise ValueError("Live capture requires macOS 14.4 or newer. Compare stereo bounces on this computer.")
    tool = ROOT / "build" / "mix-capture"
    if not tool.is_file():
        raise ValueError("Capture helper missing. Run ./setup.sh on macOS.")
    return tool


def capture(path, args):
    device = args.device or "the app's active output"
    print(f"Capturing {args.seconds}s from {device}…", flush=True)
    subprocess.run([capture_tool(), args.app, args.device, str(args.seconds), path], check=True)
    with sf.SoundFile(path) as audio:
        if (audio.channels != 2 or audio.subtype != "FLOAT" or
                audio.frames != args.seconds * audio.samplerate):
            raise ValueError(f"Incomplete or unsupported stereo capture: {path}")
        peak = 0.0
        for block in audio.blocks(blocksize=65536):
            if not np.isfinite(block).all():
                raise ValueError(f"Invalid samples in capture: {path}")
            peak = max(peak, float(np.max(np.abs(block))))
        if peak < 1e-6:
            raise ValueError(f"Silent capture. Check playback and audio recording permission. Saved WAV: {path}")
        print(f"Saved {audio.samplerate} Hz stereo float32 WAV: {path}", flush=True)


@lru_cache(maxsize=1)
def load_model():
    checkpoint = Path(os.environ.get("MIX_LISTENER_CHECKPOINT", DEFAULT_CHECKPOINT)).expanduser()
    source = MODEL_DIR / "fx_mixture_model.py"
    if not checkpoint.is_file() or not source.is_file():
        raise ValueError("Sony setup is missing. Run ./setup.sh --sony or use --signal-only.")
    try:
        import torch
        spec = importlib.util.spec_from_file_location("fx_mixture_model", source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except ImportError as error:
        raise RuntimeError("Sony dependencies are missing. Run ./setup.sh --sony.") from error

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    net = module.FxEncoderPlusPlus(embed_dim=2048, audio_clap_module=False,
                                  text_clap_module=False, extractor_module=False)
    weights = torch.load(checkpoint, weights_only=True, map_location="cpu", mmap=True)
    state = {key.removeprefix("module."): value for key, value in weights.get("state_dict", weights).items()}
    net.load_state_dict({key: state[key] for key in net.state_dict() if key in state})
    return net.eval()


def db(power):
    return 10 * np.log10(max(float(power), 1e-14))


def signal_measurements(samples, sample_rate, mean_power):
    from scipy.signal import welch

    left, right = samples.T
    mid, side = (left + right) / 2, (left - right) / 2
    frequencies, psd = welch(samples, sample_rate, nperseg=4096, axis=0)
    total = max(psd.sum(), 1e-30)

    def band_share(low, high):
        return psd[(frequencies >= low) & (frequencies < high)].sum() / total

    return (
        db(mean_power),
        db(np.max(samples**2)) - db(mean_power),
        db(np.mean(side**2)) - db(np.mean(mid**2)),
        db(np.mean(right**2)) - db(np.mean(left**2)),
        db(band_share(40, 120)),
        db(band_share(3000, 10000)),
    )


def sony_embedding(samples, sample_rate, model):
    import torch
    from scipy.signal import resample_poly

    ratio = gcd(sample_rate, MODEL_RATE)
    audio = resample_poly(samples, MODEL_RATE // ratio, sample_rate // ratio, axis=0)
    rms = np.sqrt(np.mean(audio**2))
    if not np.isfinite(rms) or rms < 1e-12:
        raise ValueError("A sampled window lost its signal during resampling.")
    audio *= 0.09 / rms
    with torch.inference_mode():
        return model.get_fx_embedding(torch.from_numpy(audio.T.copy()).float()[None]).numpy()[0]


def analyze(path, model=None):
    measurements, embeddings = [], []
    with sf.SoundFile(path) as audio:
        rate, frames = audio.samplerate, audio.frames
        window = WINDOW_SECONDS * rate
        if audio.channels != 2 or rate < 22_050 or frames < window:
            raise ValueError(f"Use stereo audio at 22.05 kHz or higher with at least two seconds: {path}")
        starts = np.linspace(0, frames - window, min(12, frames // window), dtype=int)
        for start in starts:
            audio.seek(int(start))
            samples = audio.read(window, dtype="float64")
            if len(samples) != window or not np.isfinite(samples).all():
                raise ValueError(f"Incomplete or non-finite audio in a sampled window: {path}")
            mean_power = np.mean(samples**2)
            if mean_power < 1e-12:
                continue
            measurements.append(signal_measurements(samples, rate, mean_power))
            if model is not None:
                embeddings.append(sony_embedding(samples, rate, model))
    if not measurements:
        raise ValueError(f"No signal in the sampled windows: {path}")
    embedding = None
    if embeddings:
        pooled = np.mean(embeddings, axis=0)
        norm = np.linalg.norm(pooled)
        if not np.isfinite(norm) or norm < 1e-12:
            raise ValueError(f"No usable Sony embedding: {path}")
        embedding = pooled / norm
    return np.median(measurements, axis=0), embedding, frames / rate, rate, len(measurements)


def compare(reference, take, args, folder=None):
    for path in (reference, take):
        if not path.is_file():
            raise ValueError(f"File not found: {path}")
    print("Comparing stereo excerpts locally…", flush=True)
    model = None if args.signal_only else load_model()
    a, ea, da, ra, na = analyze(reference, model)
    b, eb, dt, rt, nt = analyze(take, model)

    report = [
        "MIX LISTENER COMPARISON",
        f"Reference: {reference.resolve()} ({da:.2f}s, {ra} Hz)",
        f"Take: {take.resolve()} ({dt:.2f}s, {rt} Hz)",
        f"Analyzed {na} reference and {nt} take windows of {WINDOW_SECONDS}s.",
    ]
    if abs(da - dt) > 0.02:
        report.append("Passage lengths differ. For a controlled comparison, use the same time range.")
    if model is not None:
        similarity = np.clip(ea @ eb, -1, 1)
        report.extend([
            "", "Sony Fx-Encoder++ · RMS-matched stereo at 44.1 kHz",
            f"Mix embedding cosine similarity: {similarity:.4f}",
        ])
    report.extend([
        "", "Signal measurements · median across windows at each file's original rate · dB",
        f"{'Measure':38} {'Ref':>7} {'Take':>7} {'Change':>8}",
    ])
    for name, before, after in zip(METRICS, a, b):
        report.append(f"{name:38} {before:7.1f} {after:7.1f} {after - before:+8.1f}")

    cues = []
    for index, higher, lower in (
        (1, "higher peaks relative to the body", "lower peaks relative to the body"),
        (2, "more side energy relative to the center", "less side energy relative to the center"),
        (4, "a larger share of low-end energy", "a smaller share of low-end energy"),
        (5, "a larger share of presence-band energy", "a smaller share of presence-band energy"),
    ):
        change = b[index] - a[index]
        if abs(change) >= 1:
            cues.append(f"• Listen for {higher if change > 0 else lower} ({change:+.1f} dB).")
    report.extend(["", "Listening cues", *(cues or ["Crest, stereo side, low end, and presence each moved less than 1 dB."])])
    text = "\n".join(report)
    path = (folder or new_session()) / "Mix feedback.txt"
    path.write_text(text + "\n", encoding="utf-8")
    print(f"{text}\n\nSaved report: {path}")


def ask_file(prompt):
    raw = input(prompt).strip()
    path = Path(raw).expanduser()
    if not path.is_file():
        parts = shlex.split(raw)
        if len(parts) == 1:
            path = Path(parts[0]).expanduser()
    if not path.is_file():
        raise ValueError("File not found.")
    return path


def menu(args):
    print("Mix Listener · local stereo capture and comparison")
    reference = None
    while True:
        print("\n1  Capture reference\n2  Capture take and compare\n3  Compare two audio files"
              "\n4  Use an existing reference file\nQ  Quit")
        if reference:
            print("Current reference:", reference)
        choice = input("Choose: ").strip().lower()
        try:
            if choice == "q":
                return
            if choice == "1":
                input(f"Start playback. Press Return to capture {args.seconds}s. ")
                path = new_session() / "Reference.wav"
                capture(path, args)
                reference = path
            elif choice == "2":
                if reference is None:
                    print("Choose 1 or 4 to set a reference first.")
                    continue
                input(f"Loop the same passage. Press Return to capture {args.seconds}s. ")
                folder = new_session()
                take = folder / "Take.wav"
                capture(take, args)
                compare(reference, take, args, folder)
            elif choice == "3":
                first = ask_file("Reference file (drag here): ")
                second = ask_file("Take file (drag here): ")
                compare(first, second, args)
            elif choice == "4":
                reference = ask_file("Reference file (drag here): ")
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            print(f"Could not finish: {error}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=os.environ.get("LOGIC_AUDIO_DEVICE", ""),
                        help="exact output device name (default: detect the app's active output)")
    parser.add_argument("--app", default="com.apple.logic10",
                        help="Core Audio process bundle ID (default: Logic Pro)")
    parser.add_argument("--seconds", type=int, default=16, choices=range(2, 31),
                        metavar="2–30", help="capture length in seconds (default: 16)")
    parser.add_argument("--signal-only", action="store_true", help="compare without the optional Sony model")
    commands = parser.add_subparsers(dest="action")
    commands.add_parser("capture", help="save a native-rate stereo WAV")
    commands.add_parser("devices", help="list Core Audio output device names")
    comparison = commands.add_parser("compare", help="compare two stereo files locally")
    comparison.add_argument("reference", type=Path)
    comparison.add_argument("take", type=Path)
    args = parser.parse_args()

    if args.action == "capture":
        capture(new_session() / "Passage.wav", args)
    elif args.action == "compare":
        compare(args.reference.expanduser(), args.take.expanduser(), args)
    elif args.action == "devices":
        subprocess.run([capture_tool(), "devices"], check=True)
    elif sys.stdin.isatty():
        menu(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except EOFError:
        pass
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode if error.returncode > 0 else 1)
    except (OSError, ValueError, RuntimeError) as error:
        sys.exit(f"Could not finish: {error}")
