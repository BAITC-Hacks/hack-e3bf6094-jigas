#!/usr/bin/env python3
"""Measure one-at-a-time role-threshold sensitivity without changing release code.

The release ``assign_roles`` function is compiled from its own source after an
AST edit to exactly one comparison threshold. Its surrounding conditions,
precedence, support calculation, and output shape remain the release code.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import math
import platform
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd
import pyarrow
import networkx as nx

from hackalem.graph import basic_features, build_graph, enrich_features
from hackalem.scoring import assign_roles, compute_priority
from hackalem.validation import load, sanity_check


INPUT_NAMES = ("nodes.parquet", "edges.parquet", "transactions.parquet")
INTEGER_THRESHOLD_KEYS = frozenset(
    {
        ("coordinator", "seed_reach_count_min"),
        ("coordinator", "in_deg_min"),
        ("coordinator", "out_deg_min"),
        ("distributor", "out_deg_min"),
        ("consolidator", "in_deg_min"),
        ("terminal", "days_after_last_in_min"),
    }
)


@dataclass(frozen=True)
class Scenario:
    role: str
    parameter: str
    delta: float
    baseline: float
    requested_target: float
    target: float
    ast_target: str

    @property
    def key(self) -> str:
        return f"{self.role}.{self.parameter}:{self.delta:+.0%}"


def _number_matches(node: ast.AST, value: float) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and math.isclose(float(node.value), value, rel_tol=0.0, abs_tol=1e-12)
    )


def _name_matches(node: ast.AST, value: str) -> bool:
    return isinstance(node, ast.Name) and node.id == value


class ThresholdTransformer(ast.NodeTransformer):
    """Change one release comparison while retaining the rest of its AST."""

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.replacements = 0
        self.q90_adjustments = 0

    def _replace(self, old: ast.AST, value: float) -> ast.Constant:
        self.replacements += 1
        return ast.copy_location(ast.Constant(value=float(value)), old)

    def visit_Compare(self, node: ast.Compare):
        self.generic_visit(node)
        scenario = self.scenario
        if scenario.ast_target == "coordinator.seed_reach_count_min":
            if (
                _name_matches(node.left, "seed_reach")
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.GtE)
                and len(node.comparators) == 1
                and _number_matches(node.comparators[0], scenario.baseline)
            ):
                node.comparators[0] = self._replace(node.comparators[0], scenario.target)
        elif scenario.ast_target in (
            "coordinator.in_deg_min",
            "coordinator.out_deg_min",
            "distributor.out_deg_min",
            "consolidator.in_deg_min",
        ):
            name = {
                "coordinator.in_deg_min": "in_deg",
                "coordinator.out_deg_min": "out_deg",
                "distributor.out_deg_min": "out_deg",
                "consolidator.in_deg_min": "in_deg",
            }[scenario.ast_target]
            if (
                _name_matches(node.left, name)
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.GtE)
                and len(node.comparators) == 1
                and _number_matches(node.comparators[0], scenario.baseline)
            ):
                node.comparators[0] = self._replace(node.comparators[0], scenario.target)
        elif scenario.ast_target in (
            "transit.pass_through_min",
            "transit.pass_through_max",
        ):
            if (
                len(node.ops) == 2
                and all(isinstance(op, ast.LtE) for op in node.ops)
                and isinstance(node.comparators[0], ast.Call)
                and isinstance(node.comparators[0].func, ast.Name)
                and node.comparators[0].func.id == "float"
                and len(node.comparators[0].args) == 1
                and _name_matches(node.comparators[0].args[0], "ratio")
            ):
                if (
                    scenario.ast_target == "transit.pass_through_min"
                    and _number_matches(node.left, scenario.baseline)
                ):
                    node.left = self._replace(node.left, scenario.target)
                elif (
                    scenario.ast_target == "transit.pass_through_max"
                    and _number_matches(node.comparators[1], scenario.baseline)
                ):
                    node.comparators[1] = self._replace(node.comparators[1], scenario.target)
        elif scenario.ast_target == "terminal.days_after_last_in_min":
            if (
                isinstance(node.left, ast.Call)
                and isinstance(node.left.func, ast.Name)
                and node.left.func.id == "int"
                and len(node.left.args) == 1
                and _name_matches(node.left.args[0], "days_after")
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.GtE)
                and len(node.comparators) == 1
                and _number_matches(node.comparators[0], scenario.baseline)
            ):
                node.comparators[0] = self._replace(node.comparators[0], scenario.target)
        return node

    def visit_Assign(self, node: ast.Assign):
        self.generic_visit(node)
        if self.scenario.ast_target != "coordinator.betweenness_threshold" or not any(
            _name_matches(target, "q90_positive") for target in node.targets
        ):
            return node
        self.q90_adjustments += 1

        # Scale the exact Q90 value after release code calculates it. This
        # changes only the decision cutoff and makes returned parameters report
        # the effective threshold; it does not replace or copy the quantile.
        name = ast.Name(id="q90_positive", ctx=ast.Store())
        not_none = ast.Compare(
            left=ast.Name(id="q90_positive", ctx=ast.Load()),
            ops=[ast.IsNot()],
            comparators=[ast.Constant(value=None)],
        )
        scaled = ast.BinOp(
            left=ast.Name(id="q90_positive", ctx=ast.Load()),
            op=ast.Mult(),
            right=ast.Constant(value=float(scenario_factor(self.scenario))),
        )
        value = ast.IfExp(
            test=not_none,
            body=scaled,
            orelse=ast.Name(id="q90_positive", ctx=ast.Load()),
        )
        adjusted = ast.Assign(targets=[name], value=value)
        return [node, ast.copy_location(adjusted, node)]


def scenario_factor(scenario: Scenario) -> float:
    if scenario.ast_target == "coordinator.betweenness_threshold":
        return scenario.target
    return 1.0


def _parameter_target(baseline: float, delta: float) -> float:
    return baseline * (1.0 + delta)


def make_scenarios() -> list[Scenario]:
    definitions = [
        ("coordinator", "seed_reach_count_min", 2.0),
        ("coordinator", "in_deg_min", 2.0),
        ("coordinator", "out_deg_min", 2.0),
        ("coordinator", "betweenness_threshold", 1.0),
        ("distributor", "out_deg_min", 10.0),
        ("consolidator", "in_deg_min", 3.0),
        ("transit", "pass_through_min", 0.8),
        ("transit", "pass_through_max", 1.2),
        ("terminal", "days_after_last_in_min", 2.0),
    ]
    scenarios = []
    for role, parameter, baseline in definitions:
        target = f"{role}.{parameter}"
        for delta in (-0.20, 0.20):
            requested = _parameter_target(baseline, delta)
            value = requested
            if (role, parameter) in INTEGER_THRESHOLD_KEYS:
                # Move away from the baseline so small integer thresholds do
                # not turn a nominal 20% change into an equivalent predicate.
                value = float(math.floor(requested) if delta < 0 else math.ceil(requested))
            scenarios.append(Scenario(role, parameter, delta, baseline, requested, value, target))
    return scenarios


def compile_variant(source: str, scenario: Scenario):
    module = ast.parse(textwrap.dedent(source))
    function = next(
        (node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "assign_roles"),
        None,
    )
    if function is None:
        raise RuntimeError("could not locate release assign_roles in inspected source")
    function.name = "_assign_roles_threshold_variant"
    transformer = ThresholdTransformer(scenario)
    transformed = transformer.visit(function)
    if not isinstance(transformed, ast.FunctionDef):
        raise RuntimeError("threshold transform did not produce a function")
    if transformer.replacements != (0 if scenario.ast_target == "coordinator.betweenness_threshold" else 1):
        raise RuntimeError(
            f"expected one release threshold edit for {scenario.key}, got {transformer.replacements}"
        )
    if transformer.q90_adjustments != (1 if scenario.ast_target == "coordinator.betweenness_threshold" else 0):
        raise RuntimeError(
            f"expected one derived-Q90 adjustment for {scenario.key}, got {transformer.q90_adjustments}"
        )
    variant_module = ast.fix_missing_locations(ast.Module(body=[transformed], type_ignores=[]))
    namespace = assign_roles.__globals__.copy()
    exec(compile(variant_module, "<release-assign_roles-threshold-variant>", "exec"), namespace)
    function_variant = namespace["_assign_roles_threshold_variant"]

    def apply(frame: pd.DataFrame):
        result, parameters = function_variant(frame)
        if scenario.ast_target == "coordinator.betweenness_threshold":
            parameters["coordinator"]["betweenness_threshold"] = (
                None
                if parameters["coordinator"]["betweenness_threshold"] is None
                else float(parameters["coordinator"]["betweenness_threshold"])
            )
        else:
            parameters[scenario.role][scenario.parameter] = float(scenario.target)
        return result, parameters

    return apply


def compile_baseline_clone(source: str):
    """Compile the release function unchanged as an AST execution check."""
    module = ast.parse(textwrap.dedent(source))
    function = next(
        (node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "assign_roles"),
        None,
    )
    if function is None:
        raise RuntimeError("could not locate release assign_roles for baseline clone")
    function.name = "_assign_roles_baseline_clone"
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = assign_roles.__globals__.copy()
    exec(compile(module, "<release-assign_roles-baseline-clone>", "exec"), namespace)
    return namespace["_assign_roles_baseline_clone"]


def _state(frame: pd.DataFrame, role_function) -> dict[str, Any]:
    roles, role_parameters = role_function(frame)
    prioritized, priority_parameters = compute_priority(roles)
    priority_by_gid = prioritized.set_index("gid")["priority_score"]
    canonical_nodes = []
    for row in roles.sort_values("gid", kind="mergesort").itertuples(index=False):
        matches = [
            {"role": str(match["role"]), "support": float(match["support"]).hex()}
            for match in row.matched_roles
        ]
        canonical_nodes.append(
            {
                "gid": int(row.gid),
                "role": str(row.role),
                "role_score": float(row.role_score).hex(),
                "matched_roles": matches,
                "priority_score": float(priority_by_gid.loc[row.gid]).hex(),
            }
        )
    top20 = [int(gid) for gid in prioritized["gid"].head(20)]
    canonical = {
        "nodes": canonical_nodes,
        "top20": top20,
        "role_parameters": role_parameters,
        "priority_parameters": priority_parameters,
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "roles": roles,
        "priority": prioritized,
        "role_parameters": role_parameters,
        "top20": top20,
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
    }


def _stable_repeated_state(frame: pd.DataFrame, role_function, repeats: int, label: str):
    runs = [_state(frame, role_function) for _ in range(repeats)]
    fingerprints = {run["fingerprint"] for run in runs}
    if len(fingerprints) != 1:
        raise RuntimeError(f"non-deterministic {label}: {sorted(fingerprints)}")
    return runs[0], runs[0]["fingerprint"]


def _role_sets(roles: pd.DataFrame) -> dict[int, set[str]]:
    return {
        int(row.gid): {str(match["role"]) for match in (row.matched_roles or [])}
        for row in roles.itertuples(index=False)
    }


def _role_counts(roles: pd.DataFrame) -> dict[str, int]:
    return {str(role): int(count) for role, count in roles["role"].value_counts().items()}


def _scenario_result(baseline: dict[str, Any], variant: dict[str, Any], scenario: Scenario) -> dict[str, Any]:
    baseline_roles = baseline["roles"].set_index("gid")
    variant_roles = variant["roles"].set_index("gid")
    gids = sorted(int(gid) for gid in baseline_roles.index)
    baseline_sets = _role_sets(baseline["roles"])
    variant_sets = _role_sets(variant["roles"])
    changed_primary = [
        gid for gid in gids if baseline_roles.at[gid, "role"] != variant_roles.at[gid, "role"]
    ]
    changed_matches = [gid for gid in gids if baseline_sets[gid] != variant_sets[gid]]
    membership_edits = sum(len(baseline_sets[gid] ^ variant_sets[gid]) for gid in gids)
    baseline_top = set(baseline["top20"])
    variant_top = set(variant["top20"])
    baseline_counts = _role_counts(baseline["roles"])
    variant_counts = _role_counts(variant["roles"])
    roles = ("coordinator", "distributor", "consolidator", "transit", "terminal", "peripheral")
    count_delta = {
        role: variant_counts.get(role, 0) - baseline_counts.get(role, 0)
        for role in roles
        if variant_counts.get(role, 0) != baseline_counts.get(role, 0)
    }
    used_value = variant["role_parameters"][scenario.role][scenario.parameter]
    base_value = baseline["role_parameters"][scenario.role][scenario.parameter]
    if scenario.ast_target == "coordinator.betweenness_threshold":
        # The parameter is the actual cutoff; its derivation remains Q90.
        base_value = baseline["role_parameters"]["coordinator"]["betweenness_threshold"]
        description = f"B ≥ Q90 × {scenario.target:.1f} = {float(used_value):.8g} ({scenario.delta:+.0%})"
    else:
        description = f"{scenario.parameter} = {float(used_value):g}"
    return {
        "scenario": scenario,
        "base_value": base_value,
        "used_value": used_value,
        "requested_value": scenario.requested_target,
        "description": description,
        "primary_changed": len(changed_primary),
        "primary_changed_share": len(changed_primary) / len(gids),
        "matches_changed": len(changed_matches),
        "membership_edits": membership_edits,
        "top20_overlap": len(baseline_top & variant_top),
        "top20_overlap_pct": 100.0 * len(baseline_top & variant_top) / 20.0,
        "lost_top20": sorted(baseline_top - variant_top),
        "gained_top20": sorted(variant_top - baseline_top),
        "primary_role_count_delta": count_delta,
        "fingerprint": variant["fingerprint"],
    }


def _markdown(
    data_dir: Path,
    repeats: int,
    baseline: dict[str, Any],
    results: list[dict[str, Any]],
    source_hash: str,
    baseline_clone_hash: str,
    table_counts: dict[str, int],
) -> str:
    hashes = {
        name: hashlib.sha256((data_dir / name).read_bytes()).hexdigest()
        for name in INPUT_NAMES
    }
    nodes = len(baseline["roles"])
    top = ", ".join(str(gid) for gid in baseline["top20"])
    command = "python " + subprocess.list2cmdline(sys.argv)
    scoring_commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%H", "--", "hackalem/scoring.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    lines = [
        "# HA-12.2 — чувствительность порогов ролей",
        "",
        "Эксперимент проверяет, как отдельное изменение одного числового порога роли на ±20% влияет на назначения ролей и P-top-20. Это техническая чувствительность к фиксированному набору; она не оценивает точность ролей, риск или полезность AML.",
        "",
        "## Метод",
        "",
        "Скрипт импортирует release `hackalem.scoring.assign_roles`, получает его исходный текст через `inspect`, разбирает в AST и компилирует варианты. Для обычных порогов заменяется ровно один найденный литерал сравнения; для coordinator betweenness AST добавляет масштабирование уже рассчитанного release-кодом Q90 на 0.8 или 1.2. Условия вокруг порога, порядок ролей, поддержки и структура результата остаются из release-функции. Production-файл не меняется. Перед вариациями отдельно скомпилированный неизменённый AST-клон сравнивается с прямым вызовом release-функции по полному отпечатку результатов. Приоритет пересчитывается неизменённым `compute_priority`; P-веса не меняются.",
        "",
        "Проверены оба направления ±20% для S coordinator, входящей/исходящей степени coordinator, порога coordinator betweenness относительно Q90, out-degree distributor, in-degree consolidator, обеих границ pass-through у transit и D terminal. Каждый сценарий меняет только один порог.",
        "",
        "Для каждого входа вычислены release признаки; `assign_roles`/вариант и `compute_priority` повторены отдельно для baseline и каждого сценария. Полный отпечаток по каждому узлу (основная роль, все совпавшие роли и support, неокруглённый P) и упорядоченный top-20 сравнен между повторами.",
        "",
        "## Набор и среда",
        "",
        f"- Официальные таблицы: {table_counts['nodes']} узлов, {table_counts['edges']} агрегированных рёбер, {table_counts['transactions']} транзакций, {table_counts['seeds']} seed; повторов на состояние: {repeats}.",
        f"- Python {platform.python_version()}, pandas {pd.__version__}, NumPy {np.__version__}, PyArrow {pyarrow.__version__}, NetworkX {nx.__version__}.",
        f"- SHA-256 исходника `hackalem/scoring.py` (для функции `assign_roles`): `{source_hash}`.",
        f"- Commit, последний изменявший `hackalem/scoring.py`: `{scoring_commit}`; отпечаток baseline AST-клона совпал с прямым вызовом release `assign_roles` и `compute_priority`: `{baseline_clone_hash}`.",
        f"- SHA-256 `nodes.parquet`: `{hashes['nodes.parquet']}`.",
        f"- SHA-256 `edges.parquet`: `{hashes['edges.parquet']}`.",
        f"- SHA-256 `transactions.parquet`: `{hashes['transactions.parquet']}`.",
        f"- Baseline P-top-20, gid в порядке ранга: {top}.",
        f"- Baseline primary roles: {_format_counts(_role_counts(baseline['roles']), signed=False)}.",
        "",
        "В этой среде используются установленные версии Python 3.11.4 / NumPy 1.26.4 / NetworkX 3.2.1; SPEC требует Python 3.13, а `requirements.txt` фиксирует NumPy 2.2.6 и NetworkX 3.6.1. pandas 2.2.3 и PyArrow 25.0.1 совпадают с pin-ами. Результат фиксирует фактическую среду, не подтверждает воспроизводимость на указанном релизном окружении.",
        "",
        "## Результаты",
        "",
        "`Primary changes` — число узлов с другой основной ролью относительно baseline. `Role-membership changes` — число узлов, у которых изменился набор всех совпавших ролей; `membership edits` — число добавленных/удалённых совпадений. Top-20 overlap сравнивает узлы варианта с baseline P-top-20 из 20.",
        "",
        "| Роль / параметр | Baseline | Сценарий | Primary changes | Узлы с изменением набора ролей | Membership edits | P-top-20 overlap | Выпало / вошло в top-20 | Повторный отпечаток |",
        "|---|---:|---|---:|---:|---:|---:|---|---|",
    ]
    for result in results:
        s = result["scenario"]
        if s.ast_target == "coordinator.betweenness_threshold":
            base = f"Q90 = {float(result['base_value']):.8g}"
            test = result["description"]
        else:
            base = f"{float(result['base_value']):g}"
            if (s.role, s.parameter) in INTEGER_THRESHOLD_KEYS:
                rounding = "floor" if s.delta < 0 else "ceil"
                effective_delta = float(result["used_value"]) / float(result["base_value"]) - 1.0
                test = (
                    f"{float(result['used_value']):g} (цель {result['requested_value']:g}; "
                    f"{rounding}; фактически {effective_delta:+.0%})"
                )
            else:
                test = f"{float(result['used_value']):g} ({s.delta:+.0%})"
        role_and_parameter = f"{s.role}: `{s.parameter}`"
        lost = ", ".join(map(str, result["lost_top20"])) or "—"
        gained = ", ".join(map(str, result["gained_top20"])) or "—"
        rank_change = f"− {lost}; + {gained}"
        lines.append(
            f"| {role_and_parameter} | {base} | {test} | {result['primary_changed']} ({result['primary_changed_share']:.2%}) | {result['matches_changed']} | {result['membership_edits']} | {result['top20_overlap']}/20 ({result['top20_overlap_pct']:.0f}%) | {rank_change} | `{result['fingerprint'][:16]}` |"
        )
    lines.extend(
        [
            "",
            "Изменения primary role по сценариям:",
        ]
    )
    delta_rows = [
        f"- `{r['scenario'].key}`: {_format_counts(r['primary_role_count_delta'])}"
        for r in results
        if r["primary_role_count_delta"]
    ]
    lines.extend(delta_rows or ["- Во всех сценариях число узлов по каждой основной роли осталось прежним."])
    lines.extend(
        [
            "",
            "## Ограничения интерпретации",
            "",
            "- Пороговые счётчики целочисленные. Сначала рассчитана цель ±20%; затем для отрицательного сценария применён `floor`, для положительного — `ceil`, чтобы условие действительно сдвинулось. Таблица показывает эффективный integer-порог, точную дробную цель и фактический процентный шаг. Поэтому для малых исходных порогов фактический шаг больше 20%.",
            "- Не изменялись supports/caps, знаменатели `u`, порядок precedence, компоненты и веса priority, структурные условия (`depth < 4`, seed, положительные суммы, `out_deg = 0`) и правило `B > 0`. Это анализ порогов входа в роль, а не чувствительности всей модели.",
            "- Текст `matched_roles.reason` в AST-вариантах формируется релизной функцией и может упоминать исходный порог. В метриках эксперимента используются роли, supports и P; текст причины не служит объяснением контрфактического сценария.",
            "- Квантили Q90/Q95 в priority не заменялись. В сценарии coordinator меняется числовой cutoff от Q90; P-параметры и Q95-нормировка остаются release baseline.",
            "- Пересечение top-20 и изменения ролей — описательные метрики стабильности на этих входных данных, не ground truth, accuracy/F1 или доказательство практической AML-полезности.",
            "",
            "## Воспроизведение",
            "",
            "Фактическая команда в этой рабочей среде:",
            "",
            "```powershell",
            command,
            "```",
            "",
            "Для локальной копии с официальными файлами в `./data`:",
            "",
            "```powershell",
            "python scripts/evaluate_threshold_sensitivity.py --data ./data --output docs/threshold_stability.md --repeats 2",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _format_counts(counts: dict[str, int], *, signed: bool = True) -> str:
    if not counts:
        return "без изменений"
    if signed:
        return ", ".join(f"{role} {count:+d}" for role, count in counts.items())
    return ", ".join(f"{role} {count}" for role, count in counts.items())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data", help="directory with official Parquet inputs")
    parser.add_argument("--output", type=Path, help="write UTF-8 Markdown report here; stdout if omitted")
    parser.add_argument("--repeats", type=int, default=2, help="repeat each state for determinism (minimum 2)")
    args = parser.parse_args(argv)
    if args.repeats < 2:
        parser.error("--repeats must be at least 2")
    data_dir = args.data.resolve()
    missing = [name for name in INPUT_NAMES if not (data_dir / name).is_file()]
    if missing:
        parser.error(f"missing Parquet input(s) under {data_dir}: {', '.join(missing)}")

    edges, nodes, transactions = load(data_dir)
    sanity_check(edges, nodes, transactions)
    graph = build_graph(edges, nodes)
    features = enrich_features(graph, basic_features(graph, nodes), transactions)

    release_source = textwrap.dedent(inspect.getsource(assign_roles))
    source_hash = hashlib.sha256(release_source.encode("utf-8")).hexdigest()
    baseline, _ = _stable_repeated_state(features, assign_roles, args.repeats, "baseline")
    baseline_clone = compile_baseline_clone(release_source)
    cloned_baseline, baseline_clone_hash = _stable_repeated_state(
        features, baseline_clone, args.repeats, "unchanged AST baseline clone"
    )
    if baseline["fingerprint"] != cloned_baseline["fingerprint"]:
        raise RuntimeError("unchanged AST clone differs from direct release baseline")
    results = []
    for scenario in make_scenarios():
        variant_function = compile_variant(release_source, scenario)
        variant, _ = _stable_repeated_state(features, variant_function, args.repeats, scenario.key)
        results.append(_scenario_result(baseline, variant, scenario))

    report = _markdown(
        data_dir,
        args.repeats,
        baseline,
        results,
        source_hash,
        baseline_clone_hash,
        {
            "nodes": len(nodes),
            "edges": len(edges),
            "transactions": len(transactions),
            "seeds": int(nodes["is_seed"].sum()),
        },
    )
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8", newline="\n")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
