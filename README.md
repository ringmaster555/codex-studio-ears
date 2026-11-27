# Studio Ears for Codex

**Give Codex a way to analyze the DAW audio you’re hearing.** Studio Ears captures a stereo passage from your DAW and measures what changes between takes. Logic Pro is the default for live capture. You can also compare stereo bounces from any DAW.

## How Codex gets the audio

1. **Capture the stereo output.** When you ask Codex to capture a passage, it runs the listener on your Mac. A private, unmuted Core Audio process tap copies the first stereo pair of the selected DAW output to a 32-bit float WAV at the device’s native sample rate. When you monitor that pair, the WAV contains the same digital signal your interface receives. The tap leaves your monitoring and streaming routes in place.
2. **Analyze the same passage.** For a comparison, the listener samples up to twelve two-second windows per file. It measures level, dynamics, stereo balance, and frequency-band energy at each file’s original rate and level. With the optional Sony Fx-Encoder++ model enabled, it also resamples a copy of each window to 44.1 kHz and matches its RMS level before calculating a separate effects-similarity score.
3. **Answer the mix question.** Codex runs the command and uses its report to connect changes in the stereo signal to what you can listen for. A 3 dB gain reduction applied to identical audio should read about −3 dB in the RMS column while the level-matched Sony score stays close to 1. For a question about one instrument, compare soloed passes and then check how the change lands in the full mix.

Capture and numerical analysis run on your Mac. The listener saves captures and reports in local session folders.

## Install

Live capture requires **macOS 14.4+**, Logic Pro, and current Xcode Command Line Tools. File comparison uses Python 3.12 and also works without Logic or Core Audio.

```sh
git clone https://github.com/ringmaster555/codex-studio-ears.git
cd codex-studio-ears

# On macOS, if the Xcode Command Line Tools are missing
xcode-select --install

# If you use Homebrew, install Python 3.12 with brew install python@3.12.
# Set PYTHON to the full executable path if python3.12 is outside your PATH.
chmod +x setup.sh "Start Mix Listener.command"
./setup.sh
```

Run `./setup.sh --sony` instead to enable Sony comparisons. That step installs the inference dependencies and downloads the [official checkpoint](https://huggingface.co/yytung/fxencoder-plusplus), about 1.6 GB, from a pinned revision. Setup verifies the checkpoint and source against SHA-256. Sony's [CC BY-NC 4.0 terms](https://github.com/SonyResearch/Fx-Encoder_PlusPlus/blob/7b1e1f7efaa8a7c212ae4d62c01a5bac5f61c47b/LICENSE) apply to the optional Sony component. Signal-only mode uses the MIT-licensed listener and its standard Python dependencies.

If you already have the original Sony checkpoint, you can reuse it by setting `MIX_LISTENER_CHECKPOINT` to its absolute path both during setup and when launching the listener. The setup checks that the existing file matches the pinned checkpoint.

## Capture in Logic

1. In Logic, check **Settings → Audio → Devices** for the output device. Play or cycle the passage, using a section long enough for the capture.
2. Capture a reference, make your mix change, and capture the same passage again. Capture starts when the command runs. When bar-accurate alignment matters, bounce the same time range twice and compare those files instead.
3. On the first capture, macOS may ask for system audio recording permission for the host app. Grant permission to the app launching the listener, such as Terminal, and retry if needed. If the prompt is absent, check **System Settings → Privacy & Security**. [Apple's Core Audio tap documentation](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps).

```sh
./"Start Mix Listener.command" devices
./"Start Mix Listener.command" --seconds 16 capture
./"Start Mix Listener.command" --device "Your interface name" --seconds 16 capture
```

The listener uses Logic's active output by default and takes channels 1–2 of its first Core Audio output stream. If Logic exposes multiple outputs or auto-detection fails, pass the exact name from `devices` or set `LOGIC_AUDIO_DEVICE`. For other apps, pass `--app` with the active audio client's bundle ID. The first stream must expose at least two channels at the device's native rate as 32-bit float PCM. The private, unmuted tap leaves audio playing through the existing output and does not change device settings, virtual routing, or a remote streaming setup such as Sunshine.

Capture length is 2–30 seconds. Each capture saves a 32-bit float `Passage.wav` in a fresh folder under `Sessions/`. The command prints the full path. To save captures and reports elsewhere, set `MIX_LISTENER_RESULTS` to a directory you control.

## Compare two stereo files

Use stereo WAV or AIFF bounces at 22.05 kHz or higher, with at least two seconds of audio. For the cleanest A/B, use the same performance, arrangement, and bounce range and disable normalization during export.

```sh
# Signal measurements, with the baseline installation
./"Start Mix Listener.command" --signal-only compare "/path/Reference.wav" "/path/Take.wav"

# Sony similarity and signal measurements, after optional Sony setup
./"Start Mix Listener.command" compare "/path/Reference.wav" "/path/Take.wav"
```

Each run prints a report and saves it as `Mix feedback.txt` in a new session folder. Run the launcher with no command to open the interactive menu for capturing, reusing a reference, and comparing successive takes. With the baseline installation, run the launcher with `--signal-only` to open the menu in signal-only mode.

### Read the report

- **Sony similarity** is cosine similarity between the pooled 128-dimensional mix embeddings. Up to 12 two-second windows per file are resampled to 44.1 kHz and RMS-matched independently. Higher values indicate closer embedding directions. The score has no calibrated mix-quality threshold.
- **Signal measurements** are medians over the same non-silent windows at the original rates and levels. The report shows RMS, crest factor, side-to-mid energy, right-to-left energy, and relative energy in 40–120 Hz and 3–10 kHz. The change column is take minus reference.
- **Listening cues** point to measurements that changed by at least 1 dB. Check those cues by listening to the saved stereo audio at matched playback levels. For instrument-specific questions, compare soloed passes as well as the full mix. Crest factor alone cannot establish compressor settings.

## Use with Codex

Copy the [AGENTS.md example](examples/AGENTS.md) into your project or add it to your existing instructions. Set the absolute launcher path to your clone. For live capture, run Codex on the Mac with Logic and give it access to the launcher. For file comparison, give it access to the two local bounces. [Codex project instructions](https://developers.openai.com/codex/guides/agents-md).

Example request

> Compare these two bounces of the same chorus. Report Sony similarity and signal measurements separately. Focus on changes in the low end and stereo width.

## Verify and troubleshoot

- **No active output or silent capture**. Start playback, verify the exact device in Logic, and check system audio recording permission for the host app. If the device cannot expose a compatible first stream, use the same-range bounce workflow.
- **Sony setup missing**. Run `./setup.sh --sony` or pass `--signal-only` before `compare`.
- **Quick smoke test**. After setup, run `.venv/bin/python -m unittest discover -s tests`. The tests generate audio on demand and exercise the file comparison without the model or Logic.

## License and credits

The listener, Core Audio helper, and integration guide are [MIT-licensed](LICENSE). The optional encoder was developed by Yen-Tung Yeh, Junghyun Koo, Marco A. Martínez-Ramírez, Wei-Hsiang Liao, Yi-Hsuan Yang, and Yuki Mitsufuji. Setup fetches Sony's code and license and defers importing LAION CLAP to branches that mixture-only inference does not use. [Sony Research project](https://github.com/SonyResearch/Fx-Encoder_PlusPlus) · [Fx-Encoder++ paper](https://arxiv.org/abs/2507.02273).
