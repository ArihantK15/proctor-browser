from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LangSpec:
    source_filename: str
    compile_cmd: Optional[list]   # run in the box before run_cmd; None = interpreted
    run_cmd: list


LANGUAGES: dict[str, LangSpec] = {
    "python": LangSpec("main.py", None, ["python3", "main.py"]),
    # JS/TS/C/C++/Java added in Phase 6 — one entry each.
}


def lang_spec(language: str) -> LangSpec:
    return LANGUAGES[language.lower()]
