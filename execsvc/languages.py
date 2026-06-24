from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LangSpec:
    source_filename: str
    compile_cmd: Optional[list]   # run in the box before run_cmd; None = interpreted
    run_cmd: list
    # Minimum sandbox memory (MB). isolate caps VIRTUAL address space to --mem,
    # and the JVM reserves huge virtual regions (1GB class space, GC structures)
    # even when it uses only ~50MB physical. The other languages are happy in the
    # default cap; Java needs a floor or it dies at VM init.
    min_mem_mb: int = 0


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
    # -Xint (interpreter-only, the JVM analog of node --jitless): the sandbox
    # blocks the JIT code-cache's VIRTUAL reservation ("Could not reserve enough
    # space for code cache") even at 32MB, the same address-space limit that hit
    # node. -Xint needs no code cache. Plus SerialGC + capped heap/metaspace/stack
    # + no perf-data to keep the JVM's footprint inside the box. javac forwards
    # via -J.
    # Every default JVM reservation must be capped or it overflows the sandbox's
    # virtual-address ceiling: -Xint (no JIT code cache), SerialGC (no G1 overflow
    # mark stack), MaxMetaspaceSize + CompressedClassSpaceSize (default 1GB!),
    # ReservedCodeCacheSize, -Xmx, -Xss, no perf-data. Even capped, the JVM needs
    # ~512MB+ of virtual space → min_mem_mb floor. javac forwards flags via -J.
    "java":       LangSpec("Main.java",
                           ["javac", "-J-Xint", "-J-XX:+UseSerialGC",
                            "-J-XX:MaxMetaspaceSize=128m", "-J-XX:CompressedClassSpaceSize=64m",
                            "-J-XX:ReservedCodeCacheSize=24m", "-J-XX:-UsePerfData", "-J-Xmx128m",
                            "Main.java"],
                           ["java", "-Xint", "-XX:+UseSerialGC",
                            "-XX:MaxMetaspaceSize=128m", "-XX:CompressedClassSpaceSize=64m",
                            "-XX:ReservedCodeCacheSize=24m", "-XX:-UsePerfData", "-Xss4m",
                            "-Xmx128m", "Main"],
                           min_mem_mb=768),
}
# Common aliases the kiosk/authoring layer may send.
LANGUAGES["js"] = LANGUAGES["javascript"]
LANGUAGES["ts"] = LANGUAGES["typescript"]
LANGUAGES["c++"] = LANGUAGES["cpp"]


def lang_spec(language: str) -> LangSpec:
    return LANGUAGES[language.lower()]
