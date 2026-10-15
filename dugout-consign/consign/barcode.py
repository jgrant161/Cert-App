"""Code 128 (set B) barcodes as SVG, for item labels.

Square's barcode scanners read Code 128, and the code on the label is the
item's SKU, which is also the SKU of the item in Square, so scanning a label
at the register rings up that item.
"""

from __future__ import annotations

from html import escape

# Bar/space widths for symbol values 0-105, then the stop pattern (106).
PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
]
START_B, START_C = 104, 105
CODE_B, CODE_C = 100, 99
STOP = 106


def _digit_run(text: str, i: int) -> int:
    j = i
    while j < len(text) and text[j].isdigit():
        j += 1
    return j - i


def encode(text: str) -> list[int]:
    """Symbol values for ``text`` with start, check digit and stop.

    Uses code set B, switching to set C (two digits per symbol) for runs of
    digits long enough to make the barcode shorter, as SKUs such as DO000042 are.
    """
    if not text:
        raise ValueError("Nothing to encode.")
    for ch in text:
        if not 32 <= ord(ch) <= 126:
            raise ValueError(f"Character {ch!r} cannot be printed in a Code 128 barcode.")
    values, mode, i = [], None, 0
    while i < len(text):
        run = _digit_run(text, i)
        at_edge = i == 0 or i + run == len(text)
        if run >= (4 if at_edge else 6):
            if run % 2:                      # odd run: send the first digit in set B
                if mode != "B":
                    values.append(START_B if mode is None else CODE_B)
                    mode = "B"
                values.append(ord(text[i]) - 32)
                i += 1
                run -= 1
            values.append(START_C if mode is None else CODE_C)
            mode = "C"
            for k in range(i, i + run, 2):
                values.append(int(text[k:k + 2]))
            i += run
        else:
            if mode != "B":
                values.append(START_B if mode is None else CODE_B)
                mode = "B"
            values.append(ord(text[i]) - 32)
            i += 1
    check = (values[0] + sum(k * v for k, v in enumerate(values[1:], 1))) % 103
    return values + [check, STOP]


def modules(text: str) -> str:
    """The barcode as a string of 1 (bar) and 0 (space), one per module."""
    out = []
    for v in encode(text):
        for i, width in enumerate(PATTERNS[v]):
            out.append(("1" if i % 2 == 0 else "0") * int(width))
    return "".join(out)


def svg(text: str, height: int = 40, module: float = 1.0, quiet: int = 10, label: bool = False) -> str:
    """An SVG barcode. Width scales with the text; set the CSS width to size it on a label."""
    bits = modules(text)
    total = (len(bits) + 2 * quiet) * module
    text_h = 12 if label else 0
    rects, x, i = [], quiet * module, 0
    while i < len(bits):
        if bits[i] == "1":
            j = i
            while j < len(bits) and bits[j] == "1":
                j += 1
            rects.append(f'<rect x="{x + i * module:g}" y="0" width="{(j - i) * module:g}" height="{height}"/>')
            i = j
        else:
            i += 1
    caption = (f'<text x="{total / 2:g}" y="{height + 11}" text-anchor="middle" font-family="monospace" '
               f'font-size="11">{escape(text)}</text>') if label else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total:g} {height + text_h}" '
            f'preserveAspectRatio="none" role="img" aria-label="Barcode {escape(text)}">'
            f'<rect width="100%" height="100%" fill="#fff"/><g fill="#000">{"".join(rects)}</g>{caption}</svg>')
