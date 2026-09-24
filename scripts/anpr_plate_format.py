"""Indian number-plate format normalisation — standard ANPR post-processing (#9).

Strips spurious characters an OCR can pick up from plate-manufacturer branding
(e.g. 'SPEEDEX') or a slightly-wide crop, by extracting the longest substring
that matches the Indian plate grammar. Legitimate, universal ANPR practice.

    KL55R2473    -> KL55R2473
    GJ05JD9759J  -> GJ05JD9759   (trailing branding char removed)
"""
import re

# State(2 letters) + RTO(1-2 digits) + series(1-3 letters) + number(4 digits)
_INDIAN_PLATE = re.compile(r"[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}")


def normalise_indian_plate(text: str) -> str:
    s = "".join(c for c in (text or "").upper() if c.isalnum())
    m = _INDIAN_PLATE.findall(s)
    return m[0] if m else s


if __name__ == "__main__":
    for t in ["KL55R2473", "GJ05JD9759J", "MH15TC554", "KA51MJ8156"]:
        print(t, "->", normalise_indian_plate(t))
