import pytest

from consign import barcode

DECODE = {p: v for v, p in enumerate(barcode.PATTERNS)}


def decode(bits: str) -> str:
    """Read a module string back to text, checking the check digit."""
    runs, i = [], 0
    while i < len(bits):
        j = i
        while j < len(bits) and bits[j] == bits[i]:
            j += 1
        runs.append(str(j - i))
        i = j
    values = [DECODE["".join(runs[k:k + 6])] for k in range(0, len(runs) - 7, 6)]
    assert "".join(runs[-7:]) == barcode.PATTERNS[barcode.STOP]
    body, check = values[:-1], values[-1]
    assert (body[0] + sum(k * v for k, v in enumerate(body[1:], 1))) % 103 == check
    out, mode = [], {104: "B", 105: "C"}[body[0]]
    for v in body[1:]:
        if v == barcode.CODE_C and mode == "B":
            mode = "C"
        elif v == barcode.CODE_B and mode == "C":
            mode = "B"
        else:
            out.append(chr(v + 32) if mode == "B" else f"{v:02d}")
    return "".join(out)


@pytest.mark.parametrize("text", ["DO000042", "DO123456", "C0001", "A1234567B", "12345", "DO1234567", "hi there!"])
def test_round_trip(text):
    assert decode(barcode.modules(text)) == text


def test_every_symbol_is_eleven_modules():
    assert all(sum(map(int, p)) == 11 for p in barcode.PATTERNS[:106])


def test_sku_uses_compact_digits():
    assert len(barcode.encode("DO000042")) < len(barcode.encode("DO00004X"))


def test_svg_and_bad_input():
    assert barcode.svg("DO000001").startswith("<svg")
    with pytest.raises(ValueError):
        barcode.encode("café")
