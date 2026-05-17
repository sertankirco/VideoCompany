# SFX Assets

Place sound effect files here. Supported formats: `.wav`, `.mp3`, `.aiff`

Expected files:
- `sub-whoosh.wav`  — Transition whoosh for B-roll cuts
- `ui-click.wav`    — Subtitle pop-in sound
- `metal-hit.wav`   — High-impact cut sound for goal moments

The render engine will cycle through available SFX files and sync each file's
waveform peak to the exact frame of its associated video cut point.
