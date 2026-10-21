"""Code extraction from model output (F4.2) and candidate assembly.

Extraction failures are tracked separately from wrong answers (TEST_PLAN B3), so a model
that answers in an unexpected format shows up as an extraction problem, not a quality one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from dllm_eval.usecases.codegen.datasets import CodeProblem

_FENCE = re.compile(r"```[ \t]*(?:python3?|py)?[ \t]*\n(.*?)```", re.S | re.I)
_OPEN_FENCE = re.compile(r"```[ \t]*(?:python3?|py)?[ \t]*\n", re.I)
_CODE_HINT = re.compile(r"^\s*(def |class |import |from \S+ import )", re.M)

Method = Literal["fenced", "truncated_fence", "unfenced", "none"]


@dataclass(frozen=True)
class Extraction:
    code: str
    method: Method


def _blocks(text: str) -> tuple[list[str], Method]:
    blocks = _FENCE.findall(text)
    if blocks:
        return blocks, "fenced"
    opens = list(_OPEN_FENCE.finditer(text))
    if opens:  # output cut off before the closing fence (e.g. hit max tokens)
        return [text[opens[-1].end() :]], "truncated_fence"
    return [], "none"


def extract_code(text: str, problem: CodeProblem) -> Extraction:
    blocks, method = _blocks(text)
    if not blocks:
        if _CODE_HINT.search(text):
            return Extraction(text.strip() + "\n", "unfenced")
        return Extraction("", "none")

    if problem.kind == "function":
        wanted = re.compile(rf"^\s*def\s+{re.escape(problem.entry_point)}\s*\(", re.M)
    elif problem.kind == "leetcode":
        wanted = re.compile(r"^\s*class\s+Solution\b", re.M)
    else:  # stdin: a real program reads input or prints (not a sample I/O or math block)
        wanted = re.compile(r"input\(|sys\.stdin|print\(|sys\.stdout")
    matching = [b for b in blocks if wanted.search(b)]
    # Prefer the last block that defines what we need (models often show a draft first);
    # otherwise the longest block.
    chosen = matching[-1] if matching else max(blocks, key=len)
    return Extraction(chosen.strip("\n") + "\n", method)


def _prompt_imports(prompt: str) -> str:
    """Import lines from a HumanEval prompt header (e.g. `from typing import List`)."""
    lines = []
    for line in prompt.splitlines():
        if line.startswith(("def ", "class ", "@")):
            break
        if line.startswith(("import ", "from ")):
            lines.append(line)
    return "\n".join(lines)


def build_candidate(problem: CodeProblem, code: str) -> str:
    """Final program to execute. HumanEval answers often omit the prompt's imports, so they
    are prepended (as EvalPlus's sanitizer does); duplicates are harmless."""
    if problem.dataset == "humaneval+":
        header = _prompt_imports(problem.prompt)
        if header:
            return header + "\n\n" + code
    return code
