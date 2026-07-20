from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class FunctionBudget:
    path: str
    qualified_name: str
    max_lines: int


FUNCTION_BUDGETS = (
    FunctionBudget("main.py", "main", 1151),
    FunctionBudget("latka_jazn/core/engine.py", "JaznEngine.build_cognitive_frame", 489),
    FunctionBudget("latka_jazn/core/engine.py", "JaznEngine.process_turn", 713),
    FunctionBudget("latka_jazn/core/conversation.py", "ConversationResponder.compose", 722),
    FunctionBudget("latka_jazn/cli_commands/diagnostics.py", "doctor_payload", 180),
)

BROAD_EXCEPTION_BUDGETS = {
    "main.py": 3,
    "latka_jazn/core/engine.py": 9,
    "latka_jazn/core/runtime_daemon.py": 23,
    "latka_jazn/memory/normalization_sidecar.py": 10,
    "tools/jazn_pack_generator.py": 13,
    "latka_jazn/cli_commands/diagnostics.py": 0,
}


def _tree(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))


def _function_lengths(tree: ast.AST) -> dict[str, int]:
    result: dict[str, int] = {}
    stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            qualified_name = ".".join([*stack, node.name])
            if node.end_lineno is None:
                raise AssertionError(f"missing end_lineno for {qualified_name}")
            result[qualified_name] = node.end_lineno - node.lineno + 1
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_FunctionDef = _visit_function
        visit_AsyncFunctionDef = _visit_function

    Visitor().visit(tree)
    return result


def _broad_exception_count(tree: ast.AST) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "Exception"
    )


def test_known_hotspot_functions_do_not_grow() -> None:
    by_path: dict[str, dict[str, int]] = {}
    for budget in FUNCTION_BUDGETS:
        functions = by_path.setdefault(
            budget.path,
            _function_lengths(_tree(budget.path)),
        )
        assert budget.qualified_name in functions
        actual = functions[budget.qualified_name]
        assert actual <= budget.max_lines, (
            f"{budget.path}:{budget.qualified_name} grew to {actual} lines; "
            f"baseline budget is {budget.max_lines}. Extract a stage/helper instead."
        )


def test_broad_exception_handlers_do_not_increase() -> None:
    for relative, maximum in BROAD_EXCEPTION_BUDGETS.items():
        actual = _broad_exception_count(_tree(relative))
        assert actual <= maximum, (
            f"{relative} has {actual} broad except Exception handlers; "
            f"baseline budget is {maximum}. Catch the expected exception types."
        )
