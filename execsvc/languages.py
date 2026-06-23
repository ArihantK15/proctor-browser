from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LangSpec:
    source_filename: str
    compile_cmd: Optional[list]   # run in the box before run_cmd; None = interpreted
    run_cmd: list


# v1 language set: JS, TS, Python, C, C++, Java (C added for Indian curricula).
# Commands resolve via the sandbox PATH (set in isolate_cmd: /usr/bin:/usr/local/bin
# :/bin). Exact binary locations are verified per-host at deploy (`which ...`);
# Java requires the file be `Main.java` with a public class `Main`.
LANGUAGES: dict[str, LangSpec] = {
    "python":     LangSpec("main.py",   None,
                           ["python3", "main.py"]),
    "javascript": LangSpec("main.js",   None,
                           ["node", "main.js"]),
    "typescript": LangSpec("main.ts",   ["tsc", "main.ts", "--outFile", "main.js"],
                           ["node", "main.js"]),
    "c":          LangSpec("main.c",    ["gcc", "main.c", "-O2", "-o", "main"],
                           ["./main"]),
    "cpp":        LangSpec("main.cpp",  ["g++", "main.cpp", "-O2", "-std=c++17", "-o", "main"],
                           ["./main"]),
    "java":       LangSpec("Main.java", ["javac", "Main.java"],
                           ["java", "Main"]),
}
# Common aliases the kiosk/authoring layer may send.
LANGUAGES["js"] = LANGUAGES["javascript"]
LANGUAGES["ts"] = LANGUAGES["typescript"]
LANGUAGES["c++"] = LANGUAGES["cpp"]


def lang_spec(language: str) -> LangSpec:
    return LANGUAGES[language.lower()]
