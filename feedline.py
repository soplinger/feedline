#!/usr/bin/env python3
"""feedline: offline ham-radio antenna and feedline calculator.

Plain-text station files in; dipole and vertical lengths, feedline loss,
SWR conversions, velocity factor, quarter-wave matching, delivered power
and CI gates out. Standard library only, no network access.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

SPEED_MHZ_M = 300.0   # speed of light expressed as metre·MHz
END_FACTOR = 0.95     # end-effect shortening of a resonant element
VF_MIN, VF_MAX = 0.5, 1.0

# Band centre frequencies in MHz, used when a 'band' line gives only a name.
BANDS = {
    "160m": 1.910,
    "80m": 3.650,
    "60m": 5.357,
    "40m": 7.100,
    "30m": 10.125,
    "20m": 14.175,
    "17m": 18.118,
    "15m": 21.225,
    "12m": 24.940,
    "10m": 28.500,
    "6m": 50.150,
    "2m": 146.000,
    "70cm": 435.000,
}

# Feedline loss model: dB per 100 ft = k * sqrt(f_MHz) (skin-effect term only).
# The k values are rounded fits to typical catalogue figures; they are an
# estimate, not a datasheet. Use them for planning, not for a lab result.
CABLES = {
    "RG-58": 0.65,
    "RG-213": 0.30,
    "LMR-400": 0.14,
    "LADDER-450": 0.03,
}

SETTINGS = {"name", "vf", "cable", "run_ft", "swr_limit", "loss_limit_db"}


@dataclass
class Band:
    name: str
    freq: float
    line: int
    swr: float | None = None


@dataclass
class Station:
    name: str = "station"
    vf: float = 0.95
    cable: str = "RG-58"
    run_ft: float = 0.0
    swr_limit: float = 2.0
    loss_limit_db: float | None = None
    bands: list = field(default_factory=list)


# ---------------------------------------------------------------- physics

def dipole_total_m(freq, vf=1.0):
    """Total length of a resonant half-wave dipole in metres."""
    return SPEED_MHZ_M / 2 * vf * END_FACTOR / freq


def vertical_m(freq, vf=1.0):
    """Length of one quarter-wave vertical radiator in metres."""
    return SPEED_MHZ_M / 4 * vf * END_FACTOR / freq


def vf_from_dipole(length_m, freq):
    """Velocity factor implied by a measured, resonant dipole length."""
    return length_m * freq / (SPEED_MHZ_M / 2 * END_FACTOR)


def feedline_loss_db(cable, freq, length_ft):
    """One-way feedline loss in dB for a cable type, frequency and run length."""
    if cable not in CABLES:
        raise ValueError(f"unknown cable '{cable}'")
    return CABLES[cable] * math.sqrt(freq) * length_ft / 100


def gamma_from_swr(swr):
    if not math.isfinite(swr) or swr < 1:
        raise ValueError("SWR must be a number of at least 1")
    return (swr - 1) / (swr + 1)


def swr_from_gamma(gamma):
    if not 0 <= gamma < 1:
        raise ValueError("reflection coefficient must be in [0, 1)")
    return (1 + gamma) / (1 - gamma)


def return_loss_db(swr):
    gamma = gamma_from_swr(swr)
    return math.inf if gamma == 0 else -20 * math.log10(gamma)


def swr_from_return_loss(rl_db):
    if not math.isfinite(rl_db) or rl_db <= 0:
        raise ValueError("return loss must be a positive number of dB")
    return swr_from_gamma(10 ** (-rl_db / 20))


def mismatch_loss_db(swr):
    gamma = gamma_from_swr(swr)
    return -10 * math.log10(1 - gamma * gamma)


def delivered_percent(feed_db, swr):
    """Estimate of the share of transmitter power reaching the antenna.

    Feedline loss and mismatch loss are added in dB. The reflected wave's
    second pass through the line is ignored, so this is a planning estimate.
    """
    return 100 * 10 ** (-(feed_db + mismatch_loss_db(swr)) / 10)


def transformer_z0(z_load, z_feed):
    """Characteristic impedance of a quarter-wave matching section."""
    if z_load <= 0 or z_feed <= 0:
        raise ValueError("impedances must be positive")
    return math.sqrt(z_load * z_feed)


def quarter_wave_m(freq, vf=1.0):
    """Electrical quarter wavelength of a transmission line in metres."""
    return SPEED_MHZ_M / 4 * vf / freq


# ---------------------------------------------------------- station files

def _num(text):
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"'{text}' is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"'{text}' is not a finite number")
    return value


def _parse_setting(key, value, st):
    if key == "name":
        if not value:
            raise ValueError("name cannot be empty")
        st.name = value
    elif key == "vf":
        st.vf = _num(value)
        if not VF_MIN <= st.vf <= VF_MAX:
            raise ValueError(f"vf {value} is outside {VF_MIN}-{VF_MAX}")
    elif key == "cable":
        cable = value.upper()
        if cable not in CABLES:
            raise ValueError(f"unknown cable '{value}' (see 'feedline bands')")
        st.cable = cable
    elif key == "run_ft":
        st.run_ft = _num(value)
        if st.run_ft < 0:
            raise ValueError("run_ft cannot be negative")
    elif key == "swr_limit":
        st.swr_limit = _num(value)
        if st.swr_limit < 1:
            raise ValueError("swr_limit must be at least 1")
    elif key == "loss_limit_db":
        st.loss_limit_db = _num(value)
        if st.loss_limit_db < 0:
            raise ValueError("loss_limit_db cannot be negative")


def _parse_band(words, no, st, seen, errors):
    if len(words) < 2:
        errors.append((no, "band line needs a name, e.g. 'band 40m'"))
        return
    name = words[1].lower()
    freq, swr = None, None
    for word in words[2:]:
        if word.lower().startswith("swr="):
            try:
                swr = _num(word[4:])
            except ValueError as exc:
                errors.append((no, str(exc)))
                return
        else:
            if freq is not None:
                errors.append((no, f"frequency given twice ('{word}')"))
                return
            try:
                freq = _num(word)
            except ValueError:
                errors.append((no, f"unknown band option '{word}'"))
                return
    if freq is None:
        if name not in BANDS:
            errors.append((no, f"unknown band '{name}'; give its frequency in MHz"))
            return
        freq = BANDS[name]
    if freq <= 0:
        errors.append((no, "frequency must be positive"))
        return
    if swr is not None and swr < 1:
        errors.append((no, "swr must be at least 1"))
        return
    if name in seen:
        errors.append((no, f"band '{name}' repeated (first on line {seen[name]})"))
        return
    seen[name] = no
    st.bands.append(Band(name, freq, no, swr))


def parse_station(text):
    """Parse a station file. Returns (Station, [(line_or_None, message), ...])."""
    st, errors = Station(), []
    seen_keys, seen_bands = {}, {}
    for no, raw in enumerate(text.splitlines(), 1):
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        words = body.split()
        if words[0].lower() == "band":
            _parse_band(words, no, st, seen_bands, errors)
        elif "=" in body:
            key, value = (part.strip() for part in body.split("=", 1))
            key = key.lower()
            if key not in SETTINGS:
                errors.append((no, f"unknown setting '{key}'"))
                continue
            if key in seen_keys:
                errors.append((no, f"'{key}' repeated (first on line {seen_keys[key]})"))
                continue
            seen_keys[key] = no
            try:
                _parse_setting(key, value, st)
            except ValueError as exc:
                errors.append((no, str(exc)))
        else:
            errors.append((no, "expected 'key = value' or 'band NAME [MHZ] [swr=X]'"))
    if not st.bands and not errors:
        errors.append((None, "no band lines found"))
    return st, errors


# -------------------------------------------------------------- evaluation

def evaluate(st):
    """Return one result dict per band, with a status and any failure reasons."""
    rows = []
    for band in st.bands:
        feed = feedline_loss_db(st.cable, band.freq, st.run_ft)
        row = {
            "band": band.name,
            "freq_mhz": band.freq,
            "dipole_total_m": dipole_total_m(band.freq, st.vf),
            "dipole_leg_m": dipole_total_m(band.freq, st.vf) / 2,
            "vertical_m": vertical_m(band.freq, st.vf),
            "feedline_loss_db": feed,
            "swr": band.swr,
            "mismatch_loss_db": None,
            "return_loss_db": None,
            "delivered_pct": None,
        }
        problems = []
        total_loss = feed
        if band.swr is not None:
            ml = mismatch_loss_db(band.swr)
            row["mismatch_loss_db"] = ml
            row["return_loss_db"] = return_loss_db(band.swr)
            row["delivered_pct"] = delivered_percent(feed, band.swr)
            total_loss = feed + ml
            if band.swr > st.swr_limit:
                problems.append(f"swr {band.swr:g} > {st.swr_limit:g}")
        if st.loss_limit_db is not None and total_loss > st.loss_limit_db:
            problems.append(f"loss {total_loss:.2f} dB > {st.loss_limit_db:g} dB")
        row["status"] = "fail" if problems else "ok"
        row["problems"] = problems
        rows.append(row)
    return rows


def _cell(value, spec):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "-"
    return format(value, spec)


def _text_table(rows):
    headers = ["Band", "MHz", "Dipole m", "Leg m", "Vert m",
               "Feed dB", "SWR", "Mismatch dB", "RL dB", "Delivered", "Status"]
    body = []
    for r in rows:
        status = r["status"].upper()
        if r["problems"]:
            status += " (" + "; ".join(r["problems"]) + ")"
        body.append([
            r["band"],
            _cell(r["freq_mhz"], ".3f"),
            _cell(r["dipole_total_m"], ".3f"),
            _cell(r["dipole_leg_m"], ".3f"),
            _cell(r["vertical_m"], ".3f"),
            _cell(r["feedline_loss_db"], ".2f"),
            _cell(r["swr"], ".2f"),
            _cell(r["mismatch_loss_db"], ".2f"),
            _cell(r["return_loss_db"], ".1f"),
            _cell(r["delivered_pct"], ".1f") + ("%" if r["delivered_pct"] is not None else ""),
            status,
        ])
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *body)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    lines.append("  ".join("-" * w for w in widths))
    for row in body:
        lines.append("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    return "\n".join(lines)


def _clean(obj):
    """Make values JSON-safe: non-finite floats become null, floats are rounded."""
    if isinstance(obj, float):
        return round(obj, 4) if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _load(path):
    """Read and parse a station file, printing lint errors on failure."""
    text = Path(path).read_text(encoding="utf-8")
    st, errors = parse_station(text)
    return st, errors


def _print_errors(path, errors):
    for line, msg in errors:
        where = f"{path}:{line}" if line else str(path)
        print(f"{where}: {msg}", file=sys.stderr)


# --------------------------------------------------------------- commands

def _positive(text):
    """argparse type: a finite number greater than zero."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a number") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"{text} must be a positive number")
    return value


def _nonnegative(text):
    """argparse type: a finite number of zero or more."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a number") from None
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"{text} cannot be negative")
    return value


def cmd_dipole(args):
    total = dipole_total_m(args.freq, args.vf)
    print(f"Frequency      {args.freq:.3f} MHz")
    print(f"Velocity factor {args.vf:.3f}")
    print(f"Dipole total   {total:.3f} m ({total / 0.3048:.2f} ft)")
    print(f"Each leg       {total / 2:.3f} m ({total / 2 / 0.3048:.2f} ft)")
    print(f"Quarter-wave vertical {vertical_m(args.freq, args.vf):.3f} m")
    return 0


def cmd_swr(args):
    if args.swr is not None:
        swr = args.swr
    elif args.rl is not None:
        swr = swr_from_return_loss(args.rl)
    else:
        swr = swr_from_gamma(args.gamma)
    gamma = gamma_from_swr(swr)
    rl = return_loss_db(swr)
    print(f"SWR            {swr:.3f}")
    print(f"Reflection |Γ| {gamma:.4f}")
    print(f"Return loss    {'inf' if math.isinf(rl) else f'{rl:.2f} dB'}")
    print(f"Mismatch loss  {mismatch_loss_db(swr):.3f} dB")
    print(f"Power reflected {gamma * gamma * 100:.2f}%")
    return 0


def cmd_loss(args):
    loss = feedline_loss_db(args.cable.upper(), args.freq, args.length_ft)
    per_100 = CABLES[args.cable.upper()] * math.sqrt(args.freq)
    print(f"{args.cable.upper()} at {args.freq:g} MHz, {args.length_ft:g} ft")
    print(f"Loss per 100 ft {per_100:.3f} dB")
    print(f"Total loss      {loss:.3f} dB")
    print(f"Power remaining {100 * 10 ** (-loss / 10):.2f}%")
    return 0


def cmd_vf(args):
    vf = vf_from_dipole(args.length_m, args.freq)
    print(f"Measured resonant dipole {args.length_m:.3f} m at {args.freq:g} MHz")
    print(f"Velocity factor {vf:.4f}")
    if not VF_MIN <= vf <= VF_MAX:
        print(f"warning: {vf:.3f} is outside the usual {VF_MIN}-{VF_MAX} range; "
              "re-check the measurement", file=sys.stderr)
        return 1
    return 0


def cmd_match(args):
    z0 = transformer_z0(args.load, args.feed)
    print(f"Load {args.load:g} Ω to feed {args.feed:g} Ω")
    print(f"Quarter-wave section Z0 {z0:.2f} Ω")
    if args.freq:
        print(f"Length at {args.freq:g} MHz: {quarter_wave_m(args.freq, args.vf):.3f} m "
              f"(VF {args.vf:g})")
    return 0


def cmd_bands(_args):
    print("Band centres (MHz)")
    for name, freq in BANDS.items():
        print(f"  {name:<5} {freq:g}")
    print("Cables (dB per 100 ft = k * sqrt(MHz))")
    for name, k in CABLES.items():
        print(f"  {name:<11} k = {k}")
    return 0


def cmd_lint(args):
    st, errors = _load(args.file)
    if errors:
        _print_errors(args.file, errors)
        return 2
    print(f"{args.file}: ok ({len(st.bands)} bands)")
    return 0


def cmd_plan(args):
    st, errors = _load(args.file)
    if errors:
        _print_errors(args.file, errors)
        return 2
    rows = evaluate(st)
    if args.json:
        doc = {"station": st.name, "vf": st.vf, "cable": st.cable,
               "run_ft": st.run_ft, "bands": rows}
        print(json.dumps(_clean(doc), indent=2))
    else:
        print(f"Station: {st.name}  (VF {st.vf:g}, {st.cable}, {st.run_ft:g} ft run, "
              f"SWR limit {st.swr_limit:g})")
        print(_text_table(rows))
    return 0


def cmd_check(args):
    st, errors = _load(args.file)
    if errors:
        _print_errors(args.file, errors)
        return 2
    rows = evaluate(st)
    failing = [r for r in rows if r["status"] == "fail"]
    for r in failing:
        print(f"{args.file}: {r['band']} FAIL: {'; '.join(r['problems'])}")
    print(f"{len(rows) - len(failing)}/{len(rows)} bands pass")
    return 1 if failing else 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="feedline",
        description="Offline ham-radio antenna and feedline calculator.")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dipole", help="dipole and vertical lengths for a frequency")
    d.add_argument("freq", type=_positive, help="frequency in MHz")
    d.add_argument("--vf", type=_positive, default=1.0, help="velocity factor (default 1.0)")
    d.set_defaults(func=cmd_dipole)

    s = sub.add_parser("swr", help="convert between SWR, return loss and reflection")
    group = s.add_mutually_exclusive_group(required=True)
    group.add_argument("--swr", type=float)
    group.add_argument("--rl", type=float, help="return loss in dB")
    group.add_argument("--gamma", type=float, help="reflection coefficient magnitude")
    s.set_defaults(func=cmd_swr)

    lo = sub.add_parser("loss", help="feedline loss for a cable, frequency and length")
    lo.add_argument("cable", help="one of: " + ", ".join(CABLES))
    lo.add_argument("freq", type=_positive, help="frequency in MHz")
    lo.add_argument("length_ft", type=_nonnegative, help="run length in feet")
    lo.set_defaults(func=cmd_loss)

    v = sub.add_parser("vf", help="velocity factor from a measured resonant dipole")
    v.add_argument("length_m", type=_positive, help="measured total length in metres")
    v.add_argument("freq", type=_positive, help="resonant frequency in MHz")
    v.set_defaults(func=cmd_vf)

    m = sub.add_parser("match", help="quarter-wave transformer for a load")
    m.add_argument("--load", type=_positive, required=True, help="load impedance in ohms")
    m.add_argument("--feed", type=_positive, default=50.0, help="feed impedance (default 50)")
    m.add_argument("--freq", type=_positive, help="frequency in MHz, for the length")
    m.add_argument("--vf", type=_positive, default=0.66, help="line velocity factor")
    m.set_defaults(func=cmd_match)

    b = sub.add_parser("bands", help="list band centres and cable models")
    b.set_defaults(func=cmd_bands)

    li = sub.add_parser("lint", help="check a station file with line-numbered errors")
    li.add_argument("file")
    li.set_defaults(func=cmd_lint)

    pl = sub.add_parser("plan", help="full station report")
    pl.add_argument("file")
    pl.add_argument("--json", action="store_true", help="print JSON instead of a table")
    pl.set_defaults(func=cmd_plan)

    ch = sub.add_parser("check", help="CI gate: exit 1 if any band fails its limits")
    ch.add_argument("file")
    ch.set_defaults(func=cmd_check)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:
        print(f"feedline: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
