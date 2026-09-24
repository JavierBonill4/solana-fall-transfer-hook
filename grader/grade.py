#!/usr/bin/env python3
"""
Solana School grader — Transfer Hook.

SEALED. Its blob SHA is pinned; editing it fails the submission.

No canonical suite and no mutant pack for this one: four gates, the same four
the other assignments report.

    build    `anchor build` produced a program and an IDL
    tests    your own suite is green, with more tests in it than the starter
    surface  the IDL gained an instruction, account or field
    errors   a declared error is newly wired into the program

That last gate is defined differently here, on purpose. This assignment REUSES
the error codes the starter ships — the guide even lists their hex values — so
"a new #[error_code] variant" would fail every correct solution. What the
assignment does require is returning one where the starter returns none:
Challenge 1 rejects a non-Token-2022 mint with InvalidMint. So the gate counts
references to `ErrorCode::` outside error.rs and asks for more than the
starter has. A brand new variant satisfies it too.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "baseline.json"
RESULT = ROOT / "result.json"

CHALLENGE_ID = "transfer-hook"

SUMMARY = re.compile(
    r"test result:\s+(ok|FAILED)\.\s+(\d+)\s+passed;\s+(\d+)\s+failed"
)

notes: list[str] = []


def norm(name: str) -> str:
    """snake_case, camelCase and PascalCase collapse to the same key."""
    return name.replace("_", "").replace("-", "").lower()


def idl_surface(idl: dict) -> tuple[dict, dict, dict]:
    """
    (instructions, {account: fields}, errors), each a {normalized: as-written}
    map.

    Anchor 1.x keeps account FIELDS in `types`, leaving `accounts` as name plus
    discriminator; older layouts inline them. Read both.
    """
    instructions = {norm(i["name"]): i["name"] for i in idl.get("instructions", [])}

    fields: dict[str, dict] = {}
    for entry in idl.get("types", []) + idl.get("accounts", []):
        shape = entry.get("type")
        if not isinstance(shape, dict) or shape.get("kind") != "struct":
            continue
        got = {norm(f["name"]): f["name"] for f in shape.get("fields", []) or []}
        fields.setdefault(norm(entry["name"]), {}).update(got)

    accounts = {
        norm(a["name"]): (a["name"], fields.get(norm(a["name"]), {}))
        for a in idl.get("accounts", [])
    }
    errors = {norm(e["name"]): e["name"] for e in idl.get("errors", [])}
    return instructions, accounts, errors


def run_tests(targets: list[str], timeout: int = 1500):
    """Returns (ok, passed, failed, compiled, output)."""
    cmd = ["cargo", "test", "--quiet"]
    for t in targets:
        cmd += ["--test", t]
    cmd += ["--", "--test-threads=1"]
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return False, 0, 0, True, "timed out"

    out = proc.stdout + proc.stderr
    # A compile failure is not a test failure, and the difference matters when
    # we report back to the learner.
    compiled = "error[E" not in out and "could not compile" not in out

    passed = failed = 0
    for m in SUMMARY.finditer(out):
        passed += int(m.group(2))
        failed += int(m.group(3))
    return proc.returncode == 0, passed, failed, compiled, out


def error_uses(src: Path) -> int:
    """
    How many places outside error.rs name a declared error.

    Counting text rather than asking the IDL, because the IDL lists the
    variants that EXIST, not the ones that are actually returned — and what
    this assignment asks for is the wiring, not the declaration.
    """
    total = 0
    for f in sorted(src.rglob("*.rs")):
        if f.name == "error.rs":
            continue
        total += len(re.findall(r"ErrorCode::", f.read_text(errors="ignore")))
    return total


def main() -> int:
    base = json.loads(BASELINE.read_text())
    program_dir = base["program_dir"]
    lib_name = program_dir.replace("-", "_")

    program_so = ROOT / "target" / "deploy" / f"{lib_name}.so"
    idl_path = ROOT / "target" / "idl" / f"{lib_name}.json"
    tests_dir = ROOT / "programs" / program_dir / "tests"
    src_dir = ROOT / "programs" / program_dir / "src"

    gates = {"build": False, "tests": False, "surface": False, "errors": False}
    new_surface: list[str] = []
    new_errors: list[str] = []
    passed = failed = 0

    # ── build ────────────────────────────────────────────────────────────
    gates["build"] = program_so.exists() and idl_path.exists()
    if not gates["build"]:
        missing = "the program" if not program_so.exists() else "the IDL"
        notes.append(
            f"`anchor build` did not produce {missing}, so nothing else could "
            "be checked."
        )
        return emit(gates, passed, failed, new_surface, new_errors, base)

    # ── tests: yours, on your program ────────────────────────────────────
    #
    # Discovered, not hardcoded. Cargo treats each top-level `tests/*.rs` as
    # its own target, so a file you add is picked up without anyone being told
    # to add it to a list. `tests/helpers/mod.rs` is a module rather than a
    # target, so it is correctly not matched.
    learner = sorted(f.stem for f in tests_dir.glob("*.rs"))
    wanted = base["test_count"] + base["new_tests_required"]

    if not learner:
        notes.append(f"No test files found in programs/{program_dir}/tests/.")
    else:
        _ok, passed, failed, compiled, out = run_tests(learner)
        if not compiled:
            notes.append("Your tests do not compile against your program.")
            for line in out.splitlines():
                if line.startswith("error[") or line.startswith("error:"):
                    notes.append(line.strip()[:200])
                    break
        elif failed > 0:
            notes.append(f"{failed} test(s) failing. The suite has to be green.")
        elif passed < wanted:
            notes.append(
                f"{passed} tests passing. The starter ships {base['test_count']} "
                f"and the guide asks for {base['new_tests_required']} more "
                f"(one in Challenge 3, two in Challenge 4), so {wanted} is the bar."
            )
        gates["tests"] = failed == 0 and passed >= wanted

    # ── surface ──────────────────────────────────────────────────────────
    idl = json.loads(idl_path.read_text())
    instructions, accounts, errors = idl_surface(idl)

    base_ix = {norm(n) for n in base["instructions"]}
    base_accounts = {norm(k): {norm(f) for f in v} for k, v in base["accounts"].items()}
    base_errors = {norm(n) for n in base["errors"]}

    new_surface = sorted(
        [v for k, v in instructions.items() if k not in base_ix]
        + [label for k, (label, _) in accounts.items() if k not in base_accounts]
        + [
            f"{label}.{written}"
            for k, (label, fs) in accounts.items()
            if k in base_accounts
            for fk, written in fs.items()
            if fk not in base_accounts[k]
        ]
    )
    gates["surface"] = len(new_surface) > 0
    if gates["surface"]:
        notes.append("New on-chain surface — " + ", ".join(new_surface) + ".")
    else:
        notes.append(
            "The IDL is identical to the starter's. Challenge 2 adds `mint` to "
            "the RateLimit account; without it the limit cannot be per-mint."
        )

    # ── errors: newly declared, or newly wired ───────────────────────────
    new_errors = sorted(v for k, v in errors.items() if k not in base_errors)
    uses = error_uses(src_dir)
    gates["errors"] = len(new_errors) > 0 or uses > base["error_uses"]

    if new_errors:
        notes.append("New declared error(s): " + ", ".join(new_errors) + ".")
    elif gates["errors"]:
        notes.append(
            f"Declared errors are returned in {uses} places, up from "
            f"{base['error_uses']} in the starter."
        )
    else:
        notes.append(
            "No error is returned anywhere the starter did not already return "
            "one. Challenge 1 asks you to reject a mint that is not "
            "Token-2022 with InvalidMint."
        )

    return emit(gates, passed, failed, new_surface, new_errors, base)


def emit(gates, passed, failed, new_surface, new_errors, base) -> int:
    ordered = ["build", "tests", "surface", "errors"]
    met = sum(1 for g in ordered if gates[g])
    unmet = [g for g in ordered if not gates[g]]
    if unmet:
        notes.insert(0, "Gates not met: " + ", ".join(unmet) + ".")

    result = {
        "schema": 1,
        "challenge": CHALLENGE_ID,
        "commit_sha": os.environ.get("GITHUB_SHA", ""),
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        # No canonical suite and no mutant pack here, but the shape stays the
        # same so the site reads one format for every challenge.
        "canonical": {"passed": 0, "total": 0},
        "reference_check": {"tests_pass_on_correct_program": gates["tests"]},
        "mutation": {"killed": 0, "total": 0, "killed_ids": []},
        "gates": {
            **gates,
            "met": met,
            "of": len(ordered),
            "passing": passed,
            "failing": failed,
            "required_passing": base["test_count"] + base["new_tests_required"],
            "new_surface": new_surface,
            "new_errors": new_errors,
        },
        "notes": [n for n in notes if n],
    }

    RESULT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if met == len(ordered) else 1


if __name__ == "__main__":
    sys.exit(main())
