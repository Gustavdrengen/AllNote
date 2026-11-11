#!/usr/bin/env python3
"""diagram.py - printer-friendly SVG diagrams for the factsheets.

Two generators:

    plot   Cartesian plots: functions, points, vertical/horizontal guides.
    prim   Free-form drawings from a JSON list of primitives: lines, arrows,
           polygons, circles, arcs, angle markers, dimension lines, text.

Output is a ready-to-paste HTML snippet - an inline <svg> inside <figure>,
optionally with a caption - so the factsheet stays self-contained. Everything
is vector and stroke based: no images, no gradients, no filled backgrounds.

All lengths are millimetres. In `prim`, y grows downwards (SVG convention).

Examples
--------
    python tools/diagram.py plot --expr "sin(x)" --xmin -6.3 --xmax 6.3 \
        --ymin -1.6 --ymax 1.6 --xlabel "x" --caption "Grafen for sin x"

    python tools/diagram.py prim figur.json --caption "Kraftdiagram"
    python tools/diagram.py prim - < figur.json
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import sys
from html import escape
from pathlib import Path

INK = "#1b1b1b"
MUTED = "#5b6470"
GRID = "#dde2e7"
AXIS = "#8f98a2"
ACCENT = "#1c5f9e"
PALETTE = [ACCENT, "#b3402a", "#2f7d32", "#6a3d9a", "#a1560f"]

ALLOWED = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sqrt": math.sqrt, "abs": abs, "exp": math.exp,
    "ln": math.log, "log": math.log10,
    "floor": math.floor, "ceil": math.ceil, "round": round, "pow": pow,
    "pi": math.pi, "e": math.e, "tau": math.tau,
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def n(value: float) -> str:
    """Compact number for SVG output."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def svg_root(width: float, height: float, body: str, defs: str = "") -> str:
    definitions = f"<defs>{defs}</defs>\n" if defs else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{n(width)}mm" height="{n(height)}mm" '
        f'viewBox="0 0 {n(width)} {n(height)}" font-family="Segoe UI, Arial, Helvetica, sans-serif" '
        f'role="img">\n{definitions}{body}\n</svg>'
    )


def figure(svg: str, caption: str | None) -> str:
    if caption:
        return f'<figure class="fig">\n{svg}\n<figcaption>{escape(caption)}</figcaption>\n</figure>'
    return f'<figure class="fig">\n{svg}\n</figure>'


def arrow_head(x: float, y: float, angle: float, colour: str, size: float = 1.7) -> str:
    spread = math.radians(22)
    p1 = (x - size * math.cos(angle - spread), y - size * math.sin(angle - spread))
    p2 = (x - size * math.cos(angle + spread), y - size * math.sin(angle + spread))
    return (
        f'<polygon points="{n(x)},{n(y)} {n(p1[0])},{n(p1[1])} {n(p2[0])},{n(p2[1])}" '
        f'fill="{colour}" stroke="none"/>'
    )


def emit(snippet: str, out: str | None) -> None:
    if out:
        Path(out).write_text(snippet + "\n", encoding="utf-8")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(snippet)


# --------------------------------------------------------------------------- #
# plot
# --------------------------------------------------------------------------- #

def compile_expr(source: str):
    if "|" in source:
        sys.exit(
            f"{source!r} looks like a --series value; "
            'use --series "EXPR|LABEL|COLOR" (or a plain --expr without labels)'
        )
    expression = source.replace("^", "**")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        sys.exit(f"invalid expression {source!r}: {exc.msg}")

    blocked = (ast.Attribute, ast.Subscript, ast.Lambda, ast.Compare, ast.BoolOp,
               ast.IfExp, ast.Dict, ast.List, ast.Tuple, ast.Set, ast.NamedExpr)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id != "x" and node.id not in ALLOWED:
            sys.exit(f"unknown name in {source!r}: {node.id} (allowed: x, pi, e, sin, cos, …, log, ln, sqrt, abs, exp)")
        if isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Name) and node.func.id in ALLOWED
        ):
            sys.exit(f"only direct calls to known functions are allowed in {source!r}")
        if isinstance(node, blocked):
            sys.exit(f"expression too complex: {source!r}")

    code = compile(tree, "<expression>", "eval")

    def evaluate(x: float) -> float:
        try:
            return float(eval(code, {"__builtins__": {}}, {**ALLOWED, "x": x}))
        except Exception:
            return math.nan

    return evaluate


def nice_step(span: float, target: int = 8) -> float:
    raw = span / max(1, target)
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 5, 10):
        if raw <= factor * magnitude + 1e-12:
            return factor * magnitude
    return 10 * magnitude


def tick_values(low: float, high: float, step: float) -> list[float]:
    values = []
    value = math.ceil(low / step) * step
    while value <= high + step * 1e-9:
        values.append(0.0 if abs(value) < step * 1e-6 else value)
        value += step
    return values


def tick_label(value: float, step: float) -> str:
    decimals = 0
    while decimals < 4 and abs(round(step, decimals) - step) > 1e-9:
        decimals += 1
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def parse_series(args) -> list[tuple[str, str | None, str | None]]:
    series: list[tuple[str, str | None, str | None]] = []
    for spec in args.series:
        parts = [part.strip() for part in spec.split("|")]
        expression = parts[0]
        label = parts[1] if len(parts) > 1 and parts[1] else None
        colour = parts[2] if len(parts) > 2 and parts[2] else None
        series.append((expression, label, colour))
    for expression in args.expr:
        series.append((expression, None, None))
    return series


def plot_svg(args) -> str:
    width, height = args.width, args.height
    xmin, xmax, ymin, ymax = args.xmin, args.xmax, args.ymin, args.ymax
    if xmax <= xmin or ymax <= ymin:
        sys.exit("plot: --xmax must exceed --xmin and --ymax must exceed --ymin")

    series = parse_series(args)
    if not series and not args.point and not args.vline and not args.hline:
        sys.exit("plot: give at least one --expr, --series, --point, --vline or --hline")

    left, right, top = 11.0, 4.0, 3.0
    bottom = 7.5 + (2.6 if args.xlabel else 0.0)
    plot_w, plot_h = width - left - right, height - top - bottom
    if plot_w < 20 or plot_h < 16:
        sys.exit("plot: --width/--height too small for axes and labels")

    sx = plot_w / (xmax - xmin)
    sy = plot_h / (ymax - ymin)

    def X(value: float) -> float:
        return left + (value - xmin) * sx

    def Y(value: float) -> float:
        return top + (ymax - value) * sy

    clip = "clip" + hashlib.md5(
        f"{width}{height}{xmin}{xmax}{ymin}{ymax}".encode()
    ).hexdigest()[:8]

    body: list[str] = []
    body.append(
        f'<rect x="{n(left)}" y="{n(top)}" width="{n(plot_w)}" height="{n(plot_h)}" '
        f'fill="none" stroke="{GRID}" stroke-width="0.3"/>'
    )

    x_step = args.xtick or nice_step(xmax - xmin)
    y_step = args.ytick or nice_step(ymax - ymin)
    xs = tick_values(xmin, xmax, x_step)
    ys = tick_values(ymin, ymax, y_step)

    if args.grid:
        for value in xs:
            if abs(value) > 1e-9:
                body.append(
                    f'<line x1="{n(X(value))}" y1="{n(top)}" x2="{n(X(value))}" '
                    f'y2="{n(top + plot_h)}" stroke="{GRID}" stroke-width="0.25"/>'
                )
        for value in ys:
            if abs(value) > 1e-9:
                body.append(
                    f'<line x1="{n(left)}" y1="{n(Y(value))}" x2="{n(left + plot_w)}" '
                    f'y2="{n(Y(value))}" stroke="{GRID}" stroke-width="0.25"/>'
                )

    axis_y = Y(0) if ymin < 0 < ymax else None
    axis_x = X(0) if xmin < 0 < xmax else None
    halo = 'stroke="#fff" stroke-width="1.7" paint-order="stroke" stroke-linejoin="round"'

    # Axes and tick labels are drawn after the curves so they stay readable.
    axis_layer: list[str] = []

    # x axis + ticks + labels
    base_y = axis_y if axis_y is not None else top + plot_h
    if axis_y is not None:
        axis_layer.append(
            f'<line x1="{n(left)}" y1="{n(axis_y)}" x2="{n(left + plot_w)}" '
            f'y2="{n(axis_y)}" stroke="{INK}" stroke-width="0.45"/>'
        )
        axis_layer.append(arrow_head(left + plot_w, axis_y, 0.0, INK, 1.6))
    for value in xs:
        x = X(value)
        axis_layer.append(
            f'<line x1="{n(x)}" y1="{n(base_y)}" x2="{n(x)}" y2="{n(base_y + 1.1)}" '
            f'stroke="{AXIS}" stroke-width="0.3"/>'
        )
        if abs(value) > 1e-9 or axis_y is None:
            axis_layer.append(
                f'<text x="{n(x)}" y="{n(base_y + 4.0)}" font-size="2.2" fill="{MUTED}" '
                f'text-anchor="middle" {halo}>{escape(tick_label(value, x_step))}</text>'
            )

    # y axis + ticks + labels
    base_x = axis_x if axis_x is not None else left
    if axis_x is not None:
        axis_layer.append(
            f'<line x1="{n(axis_x)}" y1="{n(top + plot_h)}" x2="{n(axis_x)}" '
            f'y2="{n(top)}" stroke="{INK}" stroke-width="0.45"/>'
        )
        axis_layer.append(arrow_head(axis_x, top, -math.pi / 2, INK, 1.6))
    for value in ys:
        y = Y(value)
        axis_layer.append(
            f'<line x1="{n(base_x - 1.1)}" y1="{n(y)}" x2="{n(base_x)}" y2="{n(y)}" '
            f'stroke="{AXIS}" stroke-width="0.3"/>'
        )
        if abs(value) > 1e-9:
            axis_layer.append(
                f'<text x="{n(base_x - 1.8)}" y="{n(y)}" font-size="2.2" fill="{MUTED}" '
                f'text-anchor="end" dominant-baseline="middle" {halo}>{escape(tick_label(value, y_step))}</text>'
            )

    # curves
    legend: list[tuple[str, str]] = []
    clip_def = f'<clipPath id="{clip}"><rect x="{n(left)}" y="{n(top)}" width="{n(plot_w)}" height="{n(plot_h)}"/></clipPath>'
    for index, (expression, label, colour) in enumerate(series):
        evaluate = compile_expr(expression)
        colour = colour or PALETTE[index % len(PALETTE)]
        if label:
            legend.append((label, colour))

        segments: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        previous: float | None = None
        for k in range(601):
            xv = xmin + (xmax - xmin) * k / 600
            yv = evaluate(xv)
            if not math.isfinite(yv):
                if current:
                    segments.append(current)
                    current = []
                previous = None
                continue
            y_px = Y(yv)
            if previous is not None and abs(y_px - previous) > plot_h * 0.8:
                if current:
                    segments.append(current)
                current = []
            y_px = max(top - 6 * plot_h, min(top + 7 * plot_h, y_px))
            current.append((X(xv), y_px))
            previous = y_px
        if current:
            segments.append(current)

        for segment in segments:
            if len(segment) < 2:
                continue
            path = "M " + " L ".join(f"{n(px)} {n(py)}" for px, py in segment)
            body.append(
                f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="0.55" '
                f'stroke-linecap="round" clip-path="url(#{clip})"/>'
            )

    # guides
    for value in args.vline:
        body.append(
            f'<line x1="{n(X(value))}" y1="{n(top)}" x2="{n(X(value))}" y2="{n(top + plot_h)}" '
            f'stroke="{AXIS}" stroke-width="0.4" stroke-dasharray="1.2 1.1"/>'
        )
    for value in args.hline:
        body.append(
            f'<line x1="{n(left)}" y1="{n(Y(value))}" x2="{n(left + plot_w)}" y2="{n(Y(value))}" '
            f'stroke="{AXIS}" stroke-width="0.4" stroke-dasharray="1.2 1.1"/>'
        )

    body.extend(axis_layer)

    # points
    for spec in args.point:
        if "|" in spec:
            coords, _, label = spec.partition("|")
            parts = [part.strip() for part in coords.split(",")] + [label.strip()]
        else:
            parts = [part.strip() for part in spec.split(",")]
        try:
            px, py = float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            sys.exit(f"plot: --point needs X,Y[,LABEL] or X,Y|LABEL, got {spec!r}")
        body.append(
            f'<circle cx="{n(X(px))}" cy="{n(Y(py))}" r="0.85" fill="{INK}" stroke="none"/>'
        )
        if len(parts) > 2 and parts[2]:
            body.append(
                f'<text x="{n(X(px) + 1.6)}" y="{n(Y(py) - 1.7)}" font-size="2.4" '
                f'fill="{INK}" text-anchor="start">{escape(parts[2])}</text>'
            )

    # legend, top right inside the plot
    if legend and not args.no_legend:
        row = 3.0
        legend_w = max(len(text) for text, _ in legend) * 1.42 + 11
        lx = left + plot_w - legend_w - 1.6
        ly = top + 1.6
        body.append(
            f'<rect x="{n(lx)}" y="{n(ly)}" width="{n(legend_w)}" '
            f'height="{n(row * len(legend) + 1.2)}" fill="#fff" stroke="{GRID}" '
            f'stroke-width="0.25" rx="0.8"/>'
        )
        for j, (text, colour) in enumerate(legend):
            y = ly + 2.0 + j * row
            body.append(
                f'<line x1="{n(lx + 1.6)}" y1="{n(y)}" x2="{n(lx + 6.4)}" y2="{n(y)}" '
                f'stroke="{colour}" stroke-width="0.55"/>'
            )
            body.append(
                f'<text x="{n(lx + 7.4)}" y="{n(y)}" font-size="2.3" fill="{INK}" '
                f'dominant-baseline="middle">{escape(text)}</text>'
            )

    if args.xlabel:
        body.append(
            f'<text x="{n(left + plot_w / 2)}" y="{n(height - 1.4)}" font-size="2.4" '
            f'fill="{MUTED}" text-anchor="middle">{escape(args.xlabel)}</text>'
        )
    if args.ylabel:
        body.append(
            f'<text x="2.6" y="{n(top + plot_h / 2)}" font-size="2.4" fill="{MUTED}" '
            f'text-anchor="middle" transform="rotate(-90 2.6 {n(top + plot_h / 2)})">'
            f"{escape(args.ylabel)}</text>"
        )

    return svg_root(width, height, "\n".join(body), clip_def)


# --------------------------------------------------------------------------- #
# prim
# --------------------------------------------------------------------------- #

def _points(value) -> str:
    if isinstance(value, str):
        return " ".join(value.replace(";", " ").split())
    return " ".join(f"{n(p[0])},{n(p[1])}" for p in value)


def render_element(element: dict) -> str:
    kind = element.get("type", "line")
    stroke = element.get("stroke", INK)
    fill = element.get("fill", "none")
    stroke_width = element.get("sw", element.get("stroke-width", 0.35))
    extras = ""
    if element.get("dash"):
        extras += f' stroke-dasharray="{element["dash"]}"'
    if element.get("opacity") is not None:
        extras += f' opacity="{n(element["opacity"])}"'
    paint = (
        f'stroke="{stroke}" fill="{fill}" stroke-width="{n(stroke_width)}" '
        f'stroke-linejoin="round" stroke-linecap="round"{extras}'
    )

    if kind == "line":
        return (
            f'<line x1="{n(element["x1"])}" y1="{n(element["y1"])}" '
            f'x2="{n(element["x2"])}" y2="{n(element["y2"])}" {paint}/>'
        )

    if kind == "arrow":
        x1, y1 = element["x1"], element["y1"]
        x2, y2 = element["x2"], element["y2"]
        angle = math.atan2(y2 - y1, x2 - x1)
        heads = element.get("heads", "end")
        size = element.get("head", 1.7)
        out = [
            f'<line x1="{n(x1)}" y1="{n(y1)}" x2="{n(x2)}" y2="{n(y2)}" {paint}/>'
        ]
        if heads in ("end", "both"):
            out.append(arrow_head(x2, y2, angle, stroke, size))
        if heads in ("start", "both"):
            out.append(arrow_head(x1, y1, angle + math.pi, stroke, size))
        return "".join(out)

    if kind in ("poly", "polygon"):
        return f'<polygon points="{_points(element["points"])}" {paint}/>'

    if kind == "polyline":
        return f'<polyline points="{_points(element["points"])}" {paint}/>'

    if kind == "rect":
        rx = element.get("rx", 0)
        return (
            f'<rect x="{n(element["x"])}" y="{n(element["y"])}" width="{n(element["w"])}" '
            f'height="{n(element["h"])}" rx="{n(rx)}" {paint}/>'
        )

    if kind == "circle":
        return f'<circle cx="{n(element["cx"])}" cy="{n(element["cy"])}" r="{n(element["r"])}" {paint}/>'

    if kind == "ellipse":
        return (
            f'<ellipse cx="{n(element["cx"])}" cy="{n(element["cy"])}" '
            f'rx="{n(element["rx"])}" ry="{n(element["ry"])}" {paint}/>'
        )

    if kind == "arc":
        cx, cy, radius = element["cx"], element["cy"], element["r"]
        start = math.radians(element.get("from", 0))
        end = math.radians(element.get("to", 90))
        sweep_angle = math.degrees(end - start)
        large = 1 if abs(sweep_angle) > 180 else 0
        direction = 1 if sweep_angle >= 0 else 0
        x1, y1 = cx + radius * math.cos(start), cy + radius * math.sin(start)
        x2, y2 = cx + radius * math.cos(end), cy + radius * math.sin(end)
        return (
            f'<path d="M {n(x1)} {n(y1)} A {n(radius)} {n(radius)} 0 {large} {direction} '
            f'{n(x2)} {n(y2)}" {paint}/>'
        )

    if kind == "angle":
        cx, cy = element["cx"], element["cy"]
        radius = element.get("r", 5)
        start = element.get("from", 0)
        end = element.get("to", 35)
        colour = element.get("stroke", AXIS)
        arc = (
            f'<path d="M {n(cx + radius * math.cos(math.radians(start)))} '
            f'{n(cy + radius * math.sin(math.radians(start)))} A {n(radius)} {n(radius)} 0 0 1 '
            f'{n(cx + radius * math.cos(math.radians(end)))} '
            f'{n(cy + radius * math.sin(math.radians(end)))}" '
            f'stroke="{colour}" fill="none" stroke-width="{n(element.get("sw", 0.3))}"/>'
        )
        label = element.get("label")
        if not label:
            return arc
        middle = math.radians((start + end) / 2)
        label_r = radius + element.get("label_r", 2.4)
        return arc + (
            f'<text x="{n(cx + label_r * math.cos(middle))}" '
            f'y="{n(cy + label_r * math.sin(middle))}" font-size="{n(element.get("size", 2.4))}" '
            f'fill="{element.get("color", INK)}" text-anchor="middle" '
            f'dominant-baseline="middle">{escape(str(label))}</text>'
        )

    if kind == "dim":
        x1, y1 = element["x1"], element["y1"]
        x2, y2 = element["x2"], element["y2"]
        angle = math.atan2(y2 - y1, x2 - x1)
        colour = element.get("stroke", MUTED)
        size = element.get("head", 1.5)
        out = [
            f'<line x1="{n(x1)}" y1="{n(y1)}" x2="{n(x2)}" y2="{n(y2)}" '
            f'stroke="{colour}" fill="none" stroke-width="{n(element.get("sw", 0.3))}"/>',
            arrow_head(x1, y1, angle + math.pi, colour, size),
            arrow_head(x2, y2, angle, colour, size),
        ]
        label = element.get("text")
        if label:
            offset = element.get("offset", -1.9)
            mx = (x1 + x2) / 2 - math.sin(angle) * offset
            my = (y1 + y2) / 2 + math.cos(angle) * offset
            out.append(
                f'<text x="{n(mx)}" y="{n(my)}" font-size="{n(element.get("size", 2.3))}" '
                f'fill="{element.get("color", INK)}" text-anchor="middle" '
                f'dominant-baseline="middle">{escape(str(label))}</text>'
            )
        return "".join(out)

    if kind == "point":
        x, y = element["x"], element["y"]
        radius = element.get("r", 0.9)
        colour = element.get("color", element.get("fill", INK))
        out = [f'<circle cx="{n(x)}" cy="{n(y)}" r="{n(radius)}" fill="{colour}" stroke="none"/>']
        label = element.get("label")
        if label:
            dx = element.get("dx", 1.7)
            dy = element.get("dy", -1.7)
            out.append(
                f'<text x="{n(x + dx)}" y="{n(y + dy)}" font-size="{n(element.get("size", 2.5))}" '
                f'fill="{element.get("color", INK)}" text-anchor="start">{escape(str(label))}</text>'
            )
        return "".join(out)

    if kind == "text":
        x, y = element["x"], element["y"]
        size = element.get("size", 2.5)
        colour = element.get("color", INK)
        anchor = element.get("anchor", "middle")
        weight = element.get("weight", 400)
        style = ' font-style="italic"' if element.get("italic") else ""
        rotate = ""
        if element.get("rotate"):
            rotate = f' transform="rotate({n(element["rotate"])} {n(x)} {n(y)})"'
        baseline = element.get("baseline", "middle")
        return (
            f'<text x="{n(x)}" y="{n(y)}" font-size="{n(size)}" fill="{colour}" '
            f'text-anchor="{anchor}" font-weight="{weight}" dominant-baseline="{baseline}"'
            f"{style}{rotate}>{escape(str(element.get('text', '')))}</text>"
        )

    if kind == "path":
        return f'<path d="{element["d"]}" {paint}/>'

    sys.exit(f"prim: unknown element type {kind!r}")


def prim_svg(spec: dict, width: float | None, height: float | None) -> tuple[str, float, float]:
    w = width or float(spec.get("width", 88))
    h = height or float(spec.get("height", 50))
    elements = spec.get("elements", [])
    if not isinstance(elements, list):
        sys.exit("prim: 'elements' must be a list")
    body = "\n".join(render_element(element) for element in elements)
    return svg_root(w, h, body), w, h


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def cmd_plot(args) -> None:
    svg = plot_svg(args)
    emit(svg if args.bare else figure(svg, args.caption), args.out)


def cmd_prim(args) -> None:
    if args.spec == "-":
        raw = sys.stdin.read()
    else:
        path = Path(args.spec)
        if not path.exists():
            sys.exit(f"prim: no such file {path}")
        raw = path.read_text(encoding="utf-8")
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"prim: invalid JSON: {exc}")
    svg, _, _ = prim_svg(spec, args.width, args.height)
    emit(svg if args.bare else figure(svg, args.caption), args.out)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="diagram.py",
        description="Generate inline-SVG diagrams for factsheets (all units in mm).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plot = sub.add_parser("plot", help="cartesian plot of functions and points")
    plot.add_argument("--expr", action="append", default=[], metavar="F(X)",
                      help="expression in x, e.g. \"2*x+1\"; repeatable")
    plot.add_argument("--series", action="append", default=[], metavar="EXPR|LABEL|COLOR",
                      help="expression with a legend label and optional colour")
    plot.add_argument("--xmin", type=float, default=-5.0)
    plot.add_argument("--xmax", type=float, default=5.0)
    plot.add_argument("--ymin", type=float, default=-3.0)
    plot.add_argument("--ymax", type=float, default=3.0)
    plot.add_argument("--xtick", type=float, help="tick spacing on x (default: automatic)")
    plot.add_argument("--ytick", type=float, help="tick spacing on y (default: automatic)")
    plot.add_argument("--grid", dest="grid", action="store_true", default=True)
    plot.add_argument("--no-grid", dest="grid", action="store_false")
    plot.add_argument("--no-legend", action="store_true")
    plot.add_argument("--xlabel", help="axis label, e.g. \"x / s\"")
    plot.add_argument("--ylabel")
    plot.add_argument("--point", action="append", default=[], metavar="X,Y[,LABEL]")
    plot.add_argument("--vline", action="append", type=float, default=[], metavar="X")
    plot.add_argument("--hline", action="append", type=float, default=[], metavar="Y")
    plot.add_argument("--width", type=float, default=88.0, help="width in mm (default 88)")
    plot.add_argument("--height", type=float, default=52.0, help="height in mm (default 52)")
    plot.add_argument("--caption", help="figcaption text")
    plot.add_argument("--bare", action="store_true", help="print only the <svg> element")
    plot.add_argument("-o", "--out", help="write to a file instead of stdout")
    plot.set_defaults(func=cmd_plot)

    prim = sub.add_parser("prim", help="draw from a JSON element list")
    prim.add_argument("spec", help="JSON file, or - to read stdin")
    prim.add_argument("--width", type=float, help="override width in mm")
    prim.add_argument("--height", type=float, help="override height in mm")
    prim.add_argument("--caption", help="figcaption text")
    prim.add_argument("--bare", action="store_true", help="print only the <svg> element")
    prim.add_argument("-o", "--out", help="write to a file instead of stdout")
    prim.set_defaults(func=cmd_prim)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
