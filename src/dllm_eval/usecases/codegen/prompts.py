"""UC1 prompt templates (F4.2). Frozen and versioned (CLAUDE.md): changing any text below
means a new PROMPT_VERSION, never an edit in place. The same prompt goes to every model
(PRD R1, TEST_PLAN B3); there is no per-model tuning in the primary arm."""

from __future__ import annotations

from dllm_eval.schemas import Message
from dllm_eval.usecases.codegen.datasets import CodeProblem

PROMPT_VERSION = "uc1-v1"

SYSTEM = "You are an expert Python programmer."

FUNCTION_HUMANEVAL = (
    "Complete the following Python function. Respond with the complete function, including "
    "the signature and any imports it needs, in a single ```python code block. Do not include "
    "tests or example usage.\n\n```python\n{prompt}```"
)

FUNCTION_MBPP = (
    "Write a Python function named `{entry_point}` that solves the task below. Respond with "
    "the complete function and any imports it needs in a single ```python code block. Do not "
    "include tests or example usage.\n\n{prompt}"
)

STDIN = (
    "Solve the following programming problem in Python 3. Your program must read from "
    "standard input and write the answer to standard output. Respond with the complete "
    "program in a single ```python code block.\n\n{prompt}"
)

LEETCODE = (
    "Solve the following problem in Python 3 by completing the starter code. Respond with "
    "the complete `Solution` class in a single ```python code block.\n\n{prompt}\n\n"
    "Starter code:\n```python\n{starter_code}\n```"
)


def user_text(p: CodeProblem) -> str:
    if p.kind == "function":
        if p.dataset == "humaneval+":
            return FUNCTION_HUMANEVAL.format(prompt=p.prompt)
        return FUNCTION_MBPP.format(
            entry_point=p.entry_point, prompt=p.prompt.strip().strip('"').strip()
        )
    if p.kind == "stdin":
        return STDIN.format(prompt=p.prompt.strip())
    return LEETCODE.format(prompt=p.prompt.strip(), starter_code=p.starter_code.strip())


def messages(p: CodeProblem) -> list[Message]:
    return [Message(role="system", content=SYSTEM), Message(role="user", content=user_text(p))]
