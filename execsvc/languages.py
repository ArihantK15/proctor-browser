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
    # --jitless: V8 otherwise reserves a large contiguous VIRTUAL region for its
    # JIT CodeRange, which the sandbox blocks ("Failed to reserve virtual memory
    # for CodeRange", SIGTRAP) even though actual memory use is tiny (~4MB).
    # Interpreter-only mode needs no CodeRange — correct results, fine for the
    # short programs an exam runs.
    "javascript": LangSpec("main.js",   None,
                           ["node", "--jitless", "main.js"]),
    "typescript": LangSpec("main.ts",   ["tsc", "main.ts", "--outFile", "main.js"],
                           ["node", "--jitless", "main.js"]),
    "c":          LangSpec("main.c",    ["gcc", "main.c", "-O2", "-o", "main"],
                           ["./main"]),
    "cpp":        LangSpec("main.cpp",  ["g++", "main.cpp", "-O2", "-std=c++17", "-o", "main"],
                           ["./main"]),
    # Java needs explicit memory flags or the JVM's default ~240MB code-cache
    # reservation won't fit the sandbox cgroup (mem cap) and it dies at VM init
    # with "Could not reserve enough space for code cache". SerialGC + small code
    # cache + capped heap/metaspace + no perf-data (no /tmp hsperfdata write) keep
    # both javac and java comfortably inside a 256MB box. javac forwards JVM flags
    # via -J.
    "java":       LangSpec("Main.java",
                           ["javac", "-J-XX:+UseSerialGC", "-J-XX:ReservedCodeCacheSize=32m",
                            "-J-XX:MaxMetaspaceSize=96m", "-J-XX:-UsePerfData", "-J-Xmx128m",
                            "Main.java"],
                           ["java", "-XX:+UseSerialGC", "-XX:ReservedCodeCacheSize=32m",
                            "-XX:MaxMetaspaceSize=96m", "-XX:-UsePerfData", "-Xss8m", "-Xmx128m",
                            "Main"]),
}
# Common aliases the kiosk/authoring layer may send.
LANGUAGES["js"] = LANGUAGES["javascript"]
LANGUAGES["ts"] = LANGUAGES["typescript"]
LANGUAGES["c++"] = LANGUAGES["cpp"]


def lang_spec(language: str) -> LangSpec:
    return LANGUAGES[language.lower()]
