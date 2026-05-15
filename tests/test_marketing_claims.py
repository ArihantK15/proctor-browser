from pathlib import Path


MARKETING_ROOT = Path("website/src")

UNVERIFIED_MARKETING_CLAIMS = [
    "180+",
    "trusted by institutions",
    "institutions across india",
    "2.4m",
    "99.2%",
    "caught 3x",
    "zero false positives",
    "12 institutions",
    "60-80%",
    "60–80%",
    "kavita sharma",
    "partner university",
]


def test_marketing_source_has_no_unverified_trust_claims():
    offenders = []
    for path in MARKETING_ROOT.rglob("*"):
        if path.suffix not in {".jsx", ".js", ".ts", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for claim in UNVERIFIED_MARKETING_CLAIMS:
            if claim in text:
                offenders.append(f"{path}: {claim}")

    assert offenders == []
