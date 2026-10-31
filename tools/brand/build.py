#!/usr/bin/env python3
"""Build the Winnie icon set from the woodpecker mark.

`woodpecker-logo.svg` beside this file is the verbatim v6 export from the logo
project (`WLB/projects/woodpecker-logo/archive/v6`): one even-odd path whose
frame, bird and eye share a single fill, so the white areas are holes. Nothing
here redraws it. The script only

  * puts a paper disc on the mark's own frame circle,
  * recolours the path, and
  * for Android, bakes the placement into the path data (a VectorDrawable has
    no transform on a path, and baking it avoids relying on how a `<group>`
    and a `<clip-path>` compose).

The disc is the reason this file exists. The source frame is a hairline: 3.7
units of 600, i.e. 0.62% of the mark's width, so at 48 dp it is 0.3 dp and
disappears. Filling the frame's circle replaces the hairline with an edge that
survives 16-48 dp while keeping the composition (bird inscribed in a circle)
and the mark's polarity (dark bird on a light ground).

Run `python3 build.py` to rewrite the committed assets; `--sheet` additionally
writes review sheets to `.tmp/brand/` (not committed).
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

MARK_SOURCE = HERE / "woodpecker-logo.svg"

#: Winnie navy. The logo project paired it with #C8102E; the navy is the
#: surface here and the red appears only as a review variant.
INK = "#003087"
PAPER = "#FFFFFF"
CRIMSON = "#C8102E"

# The mark's frame, from the frozen geometry of the source project
# (`fitted.json`). The disc is placed on these numbers rather than on a
# re-measured bounding box, so the mark keeps its own off-centre composition.
FRAME_CENTER = (299.5520, 299.7833)
FRAME_OUTER_R = 226.8536
FRAME_INNER_R = 223.1854

#: The bird's bounding box with the frame clipped away, in source units.
#: Measured once from a 2400 px render of the clipped mark (alpha thresholded
#: against #ff0000); it is frozen here because the only consumer is the themed
#: icon, which needs the glyph centred in a container the system draws.
BIRD_BBOX = (132.50, 164.75, 460.00, 506.00)
BIRD_CENTER = ((BIRD_BBOX[0] + BIRD_BBOX[2]) / 2, (BIRD_BBOX[1] + BIRD_BBOX[3]) / 2)
BIRD_SIZE = (BIRD_BBOX[2] - BIRD_BBOX[0], BIRD_BBOX[3] - BIRD_BBOX[1])

#: Adaptive icons are a 108 dp canvas of which only the middle 72 dp is
#: guaranteed visible, and only the middle 66 dp is guaranteed uncropped.
ADAPTIVE_SIZE = 108.0
#: The launcher disc is pulled in from the 33 dp safe radius so the paper
#: circle does not sit on the launcher's own mask edge.
DISC_RADIUS = 31.0
#: The themed icon draws the bird only, 52 dp tall, centred.
MONOCHROME_SIZE = 52.0
#: Clipping the frame away exactly on its inner radius leaves the hairline's
#: antialiasing inside the clip, which reads as a faint ring on a themed icon.
#: Keep this many canvas units of clearance instead. The only thing lost is the
#: very end of the tail, where the mark draws the body into the ring anyway.
CLIP_CLEARANCE = 1.0


# --- the mark -------------------------------------------------------------


def read_mark() -> str:
    """Return the mark's path data, verbatim, from the vendored source."""
    text = MARK_SOURCE.read_text()
    match = re.search(r'\sd="([^"]+)"', text)
    if match is None:
        raise SystemExit(f"no path data in {MARK_SOURCE}")
    return match.group(1)


MARK_D = read_mark()


def fmt(value: float) -> str:
    """Compact coordinate; SVG needs no more than four decimals."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def disc(cx: float, cy: float, r: float, fill: str) -> str:
    return f'<circle cx="{fmt(cx)}" cy="{fmt(cy)}" r="{fmt(r)}" fill="{fill}"/>'


# --- geometry -------------------------------------------------------------

_TOKEN = re.compile(r"[MLAZ]|-?\d+(?:\.\d+)?")
_ARITY = {"M": 2, "L": 2, "A": 7, "Z": 0}


def transform_path(d: str, scale: float, tx: float, ty: float) -> str:
    """Scale and translate path data.

    The mark is built from absolute `M`, `L`, `A` and `Z` only, so the parse is
    exhaustive rather than tolerant: an unexpected token is an error instead of
    a silently mis-transformed icon.
    """
    tokens = _TOKEN.findall(d)
    if "".join(tokens) != re.sub(r"[\s,]+", "", d):
        raise SystemExit("path data contains commands this transform does not handle")

    out: list[str] = []
    i = 0
    while i < len(tokens):
        command = tokens[i]
        i += 1
        arity = _ARITY[command]
        args = tokens[i : i + arity]
        if len(args) != arity:
            raise SystemExit(f"{command} is missing arguments")
        i += arity
        if command in ("M", "L"):
            x, y = (float(value) for value in args)
            out.append(f"{command}{fmt(x * scale + tx)},{fmt(y * scale + ty)}")
        elif command == "A":
            rx, ry, rotation, large_arc, sweep, x, y = args
            out.append(
                f"A{fmt(float(rx) * scale)},{fmt(float(ry) * scale)} "
                f"{rotation} {large_arc} {sweep} "
                f"{fmt(float(x) * scale + tx)},{fmt(float(y) * scale + ty)}"
            )
        else:
            out.append(command)
    return " ".join(out)


def placement(cx: float, cy: float, radius: float) -> tuple[float, float, float]:
    """Scale and translation that put the mark's frame circle at (cx, cy)."""
    scale = radius / FRAME_OUTER_R
    return scale, cx - FRAME_CENTER[0] * scale, cy - FRAME_CENTER[1] * scale


def placed_mark(cx: float, cy: float, radius: float, fill: str) -> str:
    """The whole mark (frame + bird), scaled so its frame circle is `radius`."""
    scale, tx, ty = placement(cx, cy, radius)
    return (
        f'<g transform="translate({fmt(tx)},{fmt(ty)}) scale({scale:.6f})">'
        f'<path fill="{fill}" fill-rule="evenodd" d="{MARK_D}"/></g>'
    )


def bird_placement(cx: float, cy: float, height: float) -> tuple[float, float, float]:
    """Scale and translation that centre the bird's bounding box at (cx, cy)."""
    scale = height / BIRD_SIZE[1]
    return scale, cx - BIRD_CENTER[0] * scale, cy - BIRD_CENTER[1] * scale


# --- SVG compositions -----------------------------------------------------


def mark_view_box(padding: float) -> str:
    """A square view box around the frame, inflated by `padding` of its radius."""
    half = FRAME_OUTER_R * (1.0 + padding)
    x = FRAME_CENTER[0] - half
    y = FRAME_CENTER[1] - half
    return f"{fmt(x)} {fmt(y)} {fmt(2 * half)} {fmt(2 * half)}"


def flat_mark_body() -> str:
    """Paper disc + mark. Correct on light and dark surfaces unchanged.

    On a dark surface the disc reads as a light badge; on a light surface the
    disc vanishes and what remains is the mark exactly as drawn, hairline frame
    included. That is why there is one drawing rather than a light and a dark
    file.
    """
    return disc(*FRAME_CENTER, FRAME_OUTER_R, PAPER) + (
        f'<path fill="{INK}" fill-rule="evenodd" d="{MARK_D}"/>'
    )


def svg_document(body: str, size: int, view_box: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" '
        f'width="{size}" height="{size}" role="img" aria-label="Winnie">'
        f"{body}</svg>\n"
    )


# --- Android ---------------------------------------------------------------

ANDROID_RES = ROOT / "apps/android/app/src/main/res"


def circle_path(cx: float, cy: float, r: float) -> str:
    """A circle as VectorDrawable path data."""
    return (
        f"M{fmt(cx)},{fmt(cy)} m{fmt(-r)},0 "
        f"a{fmt(r)},{fmt(r)} 0 1 1 {fmt(2 * r)},0 "
        f"a{fmt(r)},{fmt(r)} 0 1 1 {fmt(-2 * r)},0 z"
    )


def adaptive_icon_xml() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!-- Generated by tools/brand/build.py; edit the script, not this file. -->\n"
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@drawable/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@drawable/ic_launcher_monochrome" />\n'
        "</adaptive-icon>\n"
    )


def disc_and_mark(size: float, radius: float) -> str:
    """Paper disc + mark, centred on a square canvas of `size`."""
    center = size / 2
    return disc(center, center, radius, PAPER) + placed_mark(
        center, center, radius, INK
    )


def launcher_body(size: float, radius: float) -> str:
    """Navy field + paper disc + mark, at any canvas size.

    Shared by the raster touch icon and the previews, so what a review shows is
    the geometry that ships.
    """
    return f'<rect width="{fmt(size)}" height="{fmt(size)}" fill="{INK}"/>' + (
        disc_and_mark(size, radius)
    )


def foreground_xml() -> str:
    scale, tx, ty = placement(ADAPTIVE_SIZE / 2, ADAPTIVE_SIZE / 2, DISC_RADIUS)
    body = transform_path(MARK_D, scale, tx, ty)
    center = ADAPTIVE_SIZE / 2
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!-- Generated by tools/brand/build.py; edit the script, not this file.\n"
        f"     Paper disc plus the mark at {fmt(DISC_RADIUS)} dp, i.e. inside the\n"
        "     66 dp safe zone of the 108 dp adaptive canvas. -->\n"
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
        f'    android:width="{fmt(ADAPTIVE_SIZE)}dp"\n'
        f'    android:height="{fmt(ADAPTIVE_SIZE)}dp"\n'
        f'    android:viewportWidth="{fmt(ADAPTIVE_SIZE)}"\n'
        f'    android:viewportHeight="{fmt(ADAPTIVE_SIZE)}">\n'
        "    <path\n"
        f'        android:fillColor="{PAPER}"\n'
        f'        android:pathData="{circle_path(center, center, DISC_RADIUS)}" />\n'
        "    <path\n"
        f'        android:fillColor="{INK}"\n'
        '        android:fillType="evenOdd"\n'
        f'        android:pathData="{body}" />\n'
        "</vector>\n"
    )


def monochrome_xml() -> str:
    scale, tx, ty = bird_placement(
        ADAPTIVE_SIZE / 2, ADAPTIVE_SIZE / 2, MONOCHROME_SIZE
    )
    body = transform_path(MARK_D, scale, tx, ty)
    frame = (FRAME_CENTER[0] * scale + tx, FRAME_CENTER[1] * scale + ty)
    clip_r = FRAME_INNER_R * scale - CLIP_CLEARANCE
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!-- Generated by tools/brand/build.py; edit the script, not this file.\n"
        "     The bird alone, centred: a themed icon is tinted by the system and\n"
        "     sits in a container the system draws, so repeating the disc here\n"
        "     would only produce a solid blob. -->\n"
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
        f'    android:width="{fmt(ADAPTIVE_SIZE)}dp"\n'
        f'    android:height="{fmt(ADAPTIVE_SIZE)}dp"\n'
        f'    android:viewportWidth="{fmt(ADAPTIVE_SIZE)}"\n'
        f'    android:viewportHeight="{fmt(ADAPTIVE_SIZE)}">\n'
        "    <clip-path\n"
        '        android:name="frame"\n'
        f'        android:pathData="{circle_path(frame[0], frame[1], clip_r)}" />\n'
        "    <path\n"
        '        android:fillColor="#000000"\n'
        '        android:fillType="evenOdd"\n'
        f'        android:pathData="{body}" />\n'
        "</vector>\n"
    )


def colors_xml() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!-- Generated by tools/brand/build.py; edit the script, not this file. -->\n"
        "<resources>\n"
        f'    <color name="ic_launcher_background">{INK}</color>\n'
        "</resources>\n"
    )


# --- writing ---------------------------------------------------------------

ADMIN_PUBLIC = ROOT / "apps/admin/public"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path.relative_to(ROOT)}")


def render_admin() -> None:
    # The tab icon wants every pixel the mark can give it; the sidebar brand
    # sits next to text and needs the breathing room. Same drawing, two frames.
    write(
        ADMIN_PUBLIC / "favicon.svg",
        svg_document(flat_mark_body(), 64, mark_view_box(0.02)),
    )
    write(
        ADMIN_PUBLIC / "logo.svg",
        svg_document(flat_mark_body(), 64, mark_view_box(0.06)),
    )


#: iOS masks the corners of the artwork itself and does not use Android's
#: adaptive safe zone, so the touch icon repeats the launcher's proportions
#: (disc = 86% of the visible width) on a full-bleed square.
TOUCH_ICON_SIZE = 180
TOUCH_ICON_RADIUS = TOUCH_ICON_SIZE / 2 * (DISC_RADIUS / (72 / 2))

#: Rendering needs a real browser: ImageMagick's bundled MSVG renderer drops
#: strokes and is not used here for anything.
FIREFOX = os.environ.get(
    "FIREFOX_BIN", "/snap/firefox/current/usr/lib/firefox/firefox"
)


def touch_icon_source() -> Path:
    path = ROOT / ".tmp/brand/apple-touch-icon.svg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        svg_document(
            launcher_body(TOUCH_ICON_SIZE, TOUCH_ICON_RADIUS),
            TOUCH_ICON_SIZE,
            f"0 0 {TOUCH_ICON_SIZE} {TOUCH_ICON_SIZE}",
        )
    )
    return path


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from a PNG's IHDR, without an image library."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path} is not a PNG")
    return struct.unpack(">II", data[16:24])


def raster_touch_icon() -> None:
    if not Path(FIREFOX).exists():
        raise SystemExit(
            f"{FIREFOX} not found; set FIREFOX_BIN to a Firefox binary to "
            "rasterise apple-touch-icon.png"
        )
    source = touch_icon_source()
    target = ADMIN_PUBLIC / "apple-touch-icon.png"
    profile = ROOT / ".cache/ff/brand-prof"
    profile.mkdir(parents=True, exist_ok=True)
    env = dict(
        os.environ,
        MOZ_HEADLESS="1",
        XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
    )
    subprocess.run(
        [
            FIREFOX,
            "--headless",
            "--no-remote",
            "--profile",
            str(profile),
            f"--window-size={TOUCH_ICON_SIZE},{TOUCH_ICON_SIZE}",
            "--screenshot",
            str(target),
            source.as_uri(),
        ],
        check=True,
        capture_output=True,
        env=env,
    )
    size = png_size(target)
    if size != (TOUCH_ICON_SIZE, TOUCH_ICON_SIZE):
        raise SystemExit(f"{target} came out {size}, not {TOUCH_ICON_SIZE}²")
    print(f"wrote {target.relative_to(ROOT)} ({size[0]}x{size[1]})")


def render_android() -> None:
    write(ANDROID_RES / "mipmap-anydpi-v26/ic_launcher.xml", adaptive_icon_xml())
    write(
        ANDROID_RES / "mipmap-anydpi-v26/ic_launcher_round.xml", adaptive_icon_xml()
    )
    write(ANDROID_RES / "drawable/ic_launcher_foreground.xml", foreground_xml())
    write(ANDROID_RES / "drawable/ic_launcher_monochrome.xml", monochrome_xml())
    write(ANDROID_RES / "values/colors.xml", colors_xml())


# --- review sheets (not shipped) ------------------------------------------


def launcher_thumbnail(spec: dict, mask: str) -> str:
    """One icon as the launcher shows it: the 72 dp visible area only.

    The field is drawn inside the mask shape rather than as a 108 dp square,
    because a square preview overstates the margin the launcher actually crops
    to and makes an over-tight disc look comfortable.
    """
    visible = 72.0
    c = ADAPTIVE_SIZE / 2
    offset = (visible - ADAPTIVE_SIZE) / 2  # 108-space -> the 72 dp preview
    if mask == "circle":
        field = disc(c, c, visible / 2, spec["field"])
    else:  # squircle, roughly the shape a Samsung launcher applies
        field = (
            f'<rect x="{fmt(c - visible / 2)}" y="{fmt(c - visible / 2)}" '
            f'width="{fmt(visible)}" height="{fmt(visible)}" '
            f'rx="{fmt(visible * 0.29)}" fill="{spec["field"]}"/>'
        )
    body = ""
    if spec.get("disc") is not None:
        body += disc(c, c, spec["disc"], spec["disc_fill"])
    body += placed_mark(c, c, spec["radius"], spec["bird"])
    return f'<g transform="translate({fmt(offset)},{fmt(offset)})">{field}{body}</g>'


def sheet(
    rows: list[tuple[str, Callable[[str], str]]], path: Path, title: str
) -> Path:
    """A grid of thumbnails: one row per composition, rendered per mask."""
    sizes = [144, 72, 48, 32, 16]
    gap, label_w, row_h = 16, 230, 190
    panel_w = sum(sizes) + gap * len(sizes)
    total_w = label_w + 2 * (panel_w + 32)
    total_h = len(rows) * row_h + 60
    parts = [
        f'<rect width="{total_w}" height="{total_h}" fill="#f6f7f9"/>',
        f'<text x="24" y="38" font-family="sans-serif" font-size="19" fill="#111">'
        f"{title}</text>",
    ]
    for index, (label, draw) in enumerate(rows):
        y = 58 + index * row_h
        parts.append(
            f'<text x="24" y="{y + 44}" font-family="sans-serif" font-size="15" '
            f'fill="#111">{label}</text>'
        )
        for panel, mask in enumerate(["circle", "squircle"]):
            px = label_w + panel * (panel_w + 32)
            parts.append(
                f'<rect x="{px - 8}" y="{y}" width="{panel_w}" height="{row_h - 14}" fill="#ffffff"/>'
            )
            x = px
            body = draw(mask)
            for size in sizes:
                parts.append(
                    f'<svg x="{x}" y="{y + 10}" width="{size}" height="{size}" '
                    f'viewBox="0 0 72 72">{body}</svg>'
                )
                x += size + gap
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
        f'viewBox="0 0 {total_w} {total_h}">{"".join(parts)}</svg>\n'
    )
    print(f"wrote {path.relative_to(ROOT)}")
    return path


def launcher_sheet() -> Path:
    def thumb(**spec: object) -> Callable[[str], str]:
        return lambda mask: launcher_thumbnail(spec, mask)

    rows = [
        (
            "A  paper disc 31 dp  (shipped)",
            thumb(field=INK, disc=31.0, disc_fill=PAPER, radius=31.0, bird=INK),
        ),
        (
            "B  paper disc 28 dp",
            thumb(field=INK, disc=28.0, disc_fill=PAPER, radius=28.0, bird=INK),
        ),
        (
            "C  white bird, no disc",
            thumb(field=INK, disc=None, disc_fill=PAPER, radius=33.0, bird=PAPER),
        ),
        (
            "D  navy disc on paper",
            thumb(field=PAPER, disc=33.0, disc_fill=INK, radius=33.0, bird=PAPER),
        ),
        (
            "E  crimson disc",
            thumb(field=INK, disc=31.0, disc_fill=CRIMSON, radius=31.0, bird=PAPER),
        ),
        (
            "F  navy bird on paper",
            thumb(field=PAPER, disc=None, disc_fill=PAPER, radius=33.0, bird=INK),
        ),
    ]
    return sheet(
        rows,
        ROOT / ".tmp/brand/launcher-sheet.svg",
        "Launcher icon — circular mask / squircle mask, at the 72 dp the launcher shows",
    )


def surface_sheet() -> Path:
    """The flat mark where it is used: tab strip and sidebar brand."""
    sizes = [96, 48, 33, 16]
    gap, label_w, row_h = 18, 210, 120
    panel_w = sum(sizes) + gap * len(sizes)
    width = label_w + 2 * (panel_w + 32)
    rows = [("favicon, 2% frame", 0.02), ("logo, 6% frame", 0.06)]
    height = 60 + row_h * len(rows)
    parts = [
        f'<rect width="{width}" height="{height}" fill="#f6f7f9"/>',
        f'<text x="24" y="38" font-family="sans-serif" font-size="19" fill="#111">'
        "Flat mark — favicon and sidebar brand, per surface</text>",
    ]
    for row, (name, padding) in enumerate(rows):
        y = 60 + row * row_h
        parts.append(
            f'<text x="24" y="{y + 40}" font-family="sans-serif" font-size="15" '
            f'fill="#111">{name}</text>'
        )
        for panel, (bg, label) in enumerate(
            [("#ffffff", "light surface"), ("#202327", "dark surface")]
        ):
            px = label_w + panel * (panel_w + 32)
            parts.append(
                f'<rect x="{px - 8}" y="{y}" width="{panel_w}" height="{row_h - 14}" fill="{bg}"/>'
            )
            parts.append(
                f'<text x="{px}" y="{y + row_h - 4}" font-family="sans-serif" '
                f'font-size="13" fill="#868e96">{label}</text>'
            )
            x = px
            for size in sizes:
                parts.append(
                    f'<svg x="{x}" y="{y + 6}" width="{size}" height="{size}" '
                    f'viewBox="{mark_view_box(padding)}">{flat_mark_body()}</svg>'
                )
                x += size + gap
    path = ROOT / ".tmp/brand/surface-sheet.svg"
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{"".join(parts)}</svg>\n'
    )
    print(f"wrote {path.relative_to(ROOT)}")
    return path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin", action="store_true", help="write admin assets")
    parser.add_argument("--android", action="store_true", help="write Android assets")
    parser.add_argument(
        "--raster", action="store_true", help="rasterise the admin touch icon"
    )
    parser.add_argument("--sheet", action="store_true", help="write the review sheets")
    args = parser.parse_args(argv)

    if not (args.admin or args.android or args.raster or args.sheet):
        args.admin = args.android = args.raster = True
    if args.admin:
        render_admin()
    if args.android:
        render_android()
    if args.raster:
        raster_touch_icon()
    if args.sheet:
        launcher_sheet()
        surface_sheet()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
