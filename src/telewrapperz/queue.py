import operator
import re
from typing import Any, Dict, List, Tuple, Union


class QueueConditionError(ValueError):
    """Raised when a queue condition is invalid or unsupported."""


_OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
}

_RESOURCE_ALIASES = {
    "cpu": "cpu",
    "ram": "memory",
    "memory": "memory",
    "mem": "memory",
    "cpu_temp": "cpu_temp",
    "temp": "cpu_temp",
    "temperature": "cpu_temp",
    "gpu": "gpu_util",
    "gpu_util": "gpu_util",
    "vram": "vram",
    "gpu_mem": "vram",
    "gpu_memory": "vram",
    "disk": "disk",
    "disk_used": "disk",
    "disk_free": "disk_free",
    "disk_free_gb": "disk_free",
}

_CLAUSE_RE = re.compile(
    r"^\s*([a-zA-Z_]+)\s*(<=|>=|<|>|==)\s*(\d+(?:\.\d+)?)\s*$"
)


def _split_clauses(expression: str) -> Tuple[List[str], str]:
    cleaned = (expression or "").strip()
    if not cleaned:
        raise QueueConditionError("Queue condition cannot be empty.")

    if " or " in cleaned.lower() or " | " in cleaned:
        clauses = re.split(r"\s+or\s+|\s*\|\s*", cleaned, flags=re.IGNORECASE)
        combinator = "any"
    else:
        clauses = re.split(r"\s+and\s+|\s*,\s*|\s*&&\s*", cleaned, flags=re.IGNORECASE)
        combinator = "all"

    clauses = [c.strip() for c in clauses if c.strip()]
    if not clauses:
        raise QueueConditionError(
            "Invalid condition: provide at least one clause (e.g. ram<80, cpu<50)."
        )
    return clauses, combinator


def parse_single_clause(clause: str) -> Tuple[str, str, float]:
    match = _CLAUSE_RE.match(clause)
    if not match:
        raise QueueConditionError(
            f"Invalid clause: '{clause}'. Use format like cpu<50, ram<80, vram<90, cpu_temp<75."
        )
    raw_resource, op, raw_value = match.groups()
    resource_key = raw_resource.lower()
    if resource_key not in _RESOURCE_ALIASES:
        valid = ", ".join(sorted(set(_RESOURCE_ALIASES.keys())))
        raise QueueConditionError(
            f"Unrecognized resource: '{raw_resource}'. Valid resources: {valid}."
        )
    return _RESOURCE_ALIASES[resource_key], op, float(raw_value)


def parse_condition(expression: str):
    clauses, combinator = _split_clauses(expression)
    parsed = [parse_single_clause(clause) for clause in clauses]
    if len(parsed) == 1:
        return parsed[0]
    return parsed, combinator


def validate_condition(expression: str) -> None:
    """Raises QueueConditionError if the expression cannot be parsed."""
    parse_condition(expression)


def _evaluate_single(
    resource: str,
    op: str,
    threshold: float,
    metrics: Dict[str, Any],
) -> bool:
    val = metrics.get(resource)
    if val is None:
        raise QueueConditionError(
            f"Metric '{resource}' is not available on this system to evaluate condition."
        )
    cmp_fn = _OPERATORS[op]
    return bool(cmp_fn(val, threshold))


def condition_is_met(
    expression: str,
    metrics_or_cpu: Union[Dict[str, Any], float],
    memory_percent: Union[float, None] = None,
) -> bool:
    """Evaluates whether the specified condition is met against current metrics."""
    if isinstance(metrics_or_cpu, dict):
        metrics = metrics_or_cpu
    else:
        metrics = {
            "cpu": float(metrics_or_cpu),
            "memory": float(memory_percent if memory_percent is not None else 0.0),
        }

    clauses, combinator = _split_clauses(expression)
    results = [
        _evaluate_single(res, op, threshold, metrics)
        for res, op, threshold in (parse_single_clause(c) for c in clauses)
    ]

    if combinator == "any":
        return any(results)
    return all(results)
