# Feedline

**Offline ham-radio antenna and feedline calculator.** Describe your station in one plain-text file and get dipole and vertical lengths, feedline loss, SWR conversions, delivered power and a CI-style pass/fail gate, with no accounts, servers or network calls.

Feedline is a single standard-library Python module. Its only dependency is `pytest` if you want to use it to run the tests.

## Features

- **Dipole lengths per band**: total and per-leg lengths for a resonant half-wave dipole, with velocity factor and end-effect shortening applied.
- **Quarter-wave verticals**: radiator length for any frequency and velocity factor.
- **Feedline loss**: one-way loss for RG-58, RG-213, LMR-400 or 450 Ω ladder line at any frequency and run length.
- **SWR conversions**: convert between SWR, return loss (dB), reflection coefficient and mismatch loss, in either direction.
- **Delivered-power estimate**: the share of transmitter power that reaches the antenna after feedline and mismatch loss.
- **Velocity-factor solver**: work out a cable's velocity factor from a measured, resonant dipole.
- **Quarter-wave matching**: the transformer impedance and physical length for matching a load to a feed impedance.
- **Plain-text station files**: `key = value` settings plus `band` lines, with `#` comments.
- **Band library**: a band line can be just a name (`band 20m`), which uses the band centre frequency.
- **Line-numbered lint**: every bad setting, unknown band, repeated key or invalid SWR is reported as `file:line: message`.
- **Station report**: a per-band table covering lengths, feed loss, SWR, mismatch loss, return loss, delivered power and status.
- **JSON output**: `plan --json` prints the same report as machine-readable JSON, with infinite values as `null`.
- **CI gate**: `check` exits `0` when every band passes, `1` when a band breaks its SWR or loss limit, and `2` for input errors.
- **Offline and dependency-free**: standard library only, no network access.

## Quick start

Clone or download this folder, then run the example station:

```sh
python3 feedline.py plan examples/backyard.ant
```

The example file is `examples/backyard.ant`:

```ini
name = Backyard dipole
vf = 0.95
cable = LMR-400
run_ft = 60
swr_limit = 2.0
loss_limit_db = 2.5

band 40m swr=1.4
band 20m swr=1.2
band 15m swr=1.8
band 10m swr=2.6
band 6m
```

Other one-off calculations:

```sh
python3 feedline.py dipole 14.175 --vf 0.95     # dipole and vertical lengths at 20 m
python3 feedline.py swr --rl 14                  # SWR from a 14 dB return loss
python3 feedline.py loss LMR-400 28.5 60         # feedline loss, 60 ft at 10 m
python3 feedline.py vf 9.57 14.2                 # velocity factor from a measured dipole
python3 feedline.py match --load 25 --freq 14.2  # quarter-wave transformer for 25 Ω
```

## How to run

Requires Python 3.10 or newer.

| Command | What it does |
| --- | --- |
| `dipole FREQ [--vf V]` | Dipole total and leg lengths, plus vertical length |
| `swr (--swr X \| --rl DB \| --gamma G)` | Convert between SWR, return loss, reflection and mismatch loss |
| `loss CABLE FREQ FEET` | Feedline loss and remaining power |
| `vf LENGTH_M FREQ` | Velocity factor from a measured resonant dipole. Prints a warning and exits `1` if the result falls outside 0.5–1.0 |
| `match --load R [--feed Z] [--freq F] [--vf V]` | Quarter-wave transformer impedance and length |
| `bands` | List band centres and cable models |
| `lint FILE` | Check a station file, printing `file:line` errors |
| `plan FILE [--json]` | Full per-band report |
| `check FILE` | CI gate. Exit `0` pass, `1` a band fails, `2` input error |

The cable loss model is `dB per 100 ft = k × √MHz`, with k = 0.65 (RG-58), 0.30 (RG-213), 0.14 (LMR-400) and 0.03 (450 Ω ladder). These are rounded estimates for planning. Check a datasheet before relying on them for a specific installation.

## Station file format

- `key = value` settings: `name`, `vf` (0.5–1.0), `cable`, `run_ft`, `swr_limit` (at least 1), `loss_limit_db`. Each may appear once.
- `band NAME [MHZ] [swr=X]`: the frequency is optional for the standard bands listed by `bands`. Each band name may appear once.
- `#` starts a comment, and blank lines are ignored.

## How to test

```sh
python3 -m pytest -q tests
```

Or, without pytest:

```sh
python3 -m unittest discover -s tests -v
```

The tests cover the dipole, vertical and velocity-factor maths, SWR and return-loss conversions, feedline loss scaling, matching, station parsing with line numbers, evaluation limits, and the CLI exit codes.

## Project layout

```
feedline.py              the whole tool (CLI and calculations)
examples/backyard.ant    a sample station file
tests/test_feedline.py   unit and CLI tests
```

## Notes and limits

- Dipole and vertical lengths use a 0.95 end-effect factor. Real installations need trimming on the bench with an analyser.
- The delivered-power estimate ignores the second pass of reflected power through the line. Treat it as a planning figure.
- Band centres are fixed reference points, not band edges. Check your licence's privileges for exact limits.
