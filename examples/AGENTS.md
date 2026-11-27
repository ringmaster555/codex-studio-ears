# Logic Pro listening and mix comparisons

Replace `/absolute/path/to/codex-studio-ears` with the path to your clone.

- For a requested live passage, confirm playback is ready and run `"/absolute/path/to/codex-studio-ears/Start Mix Listener.command" --seconds 16 capture`. Use the requested duration when supplied.
- When the user describes a sound they want, suggest one specific, reversible DAW adjustment and explain what to listen for. Compare the same passage after they try it, and use their listening notes and the report to suggest the next adjustment.
- For A/B feedback, run `"/absolute/path/to/codex-studio-ears/Start Mix Listener.command" compare "REFERENCE.wav" "TAKE.wav"`. Prefer the same musical passage and performance. Use exact-range stereo bounces when alignment matters.
- Use `--signal-only` before `compare` when the Sony model is unavailable or the user requests signal measurements alone. Say when Sony similarity is unavailable.
- Keep Sony Fx-Encoder++ similarity separate from original-level signal measurements. Give take-minus-reference changes and describe what the user can listen for. Treat model similarity as a reference comparison and avoid inferring plugin settings from the metrics alone.
- Preserve existing audio devices and routing, including virtual outputs and remote streaming. Use `devices` and `--device` if needed. Use a stereo bounce if a compatible live tap is unavailable.
- Keep the audio and reports local unless the user explicitly asks to share them. Include the path to the saved capture or report.
