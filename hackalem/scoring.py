"""Assign explainable roles and a review priority."""

import numpy as np
import pandas as pd

ROLES = ["coordinator", "distributor", "consolidator", "transit", "terminal", "peripheral"]
ROLE_EVIDENCE_CAVEATS = {
    "coordinator": "гипотеза структурной координации",
    "distributor": "цель переводов неизвестна",
    "consolidator": "общность контроля не установлена",
    "transit": "баланс неполон, происхождение неизвестно",
    "terminal": "наблюдается только период и фрагмент",
}


def _display_number(value) -> str:
    """Format a finite number compactly while keeping small values visible."""
    if value is None or pd.isna(value):
        return "—"
    number = float(value)
    if not np.isfinite(number):
        return "—"
    return format(number, ".8g")


def _limitation(row) -> str:
    if bool(row.boundary):
        return "depth=4: исходящие не наблюдались"
    if bool(row.isolated):
        return "нет наблюдаемых рёбер"
    if bool(row.is_seed):
        return "входящие связи seed могут быть неполными"
    if int(row.seed_reach_count) == 0:
        return "seed не достигнут за 1–4 шага"
    return "учтены только наблюдаемые переводы"


def assign_roles(df: pd.DataFrame):
    """Apply the documented role rules and return their concrete parameters."""
    required = [
        "seed_reach_count", "betweenness", "in_deg", "out_deg", "in_tiyn",
        "out_tiyn", "pass_through", "depth", "is_seed", "days_after_last_in",
    ]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"role assignment is missing required features: {', '.join(missing)}")

    positive_b = pd.to_numeric(df["betweenness"], errors="coerce")
    positive_b = positive_b[np.isfinite(positive_b) & positive_b.gt(0)]
    q90_positive = (
        float(np.quantile(positive_b.to_numpy(dtype=np.float64), 0.90, method="linear"))
        if len(positive_b)
        else None
    )

    caps = {
        "coordinator": 0.60,
        "distributor": 0.90,
        "consolidator": 0.90,
        "transit": 0.65,
        "terminal": 0.50,
    }
    role_parameters = {
        "explanation_version": "1.0",
        "matched_role_fields": ["role", "support", "reason"],
        "precedence": list(ROLES),
        "quantile_method": "linear",
        "coordinator": {
            "seed_reach_count_min": 2,
            "in_deg_min": 2,
            "out_deg_min": 2,
            "betweenness_positive_quantile": 0.90,
            "betweenness_threshold": q90_positive,
            "support_cap": caps["coordinator"],
            "u": "min(1, seed_reach_count / 4)",
        },
        "distributor": {
            "out_deg_min": 10,
            "support_cap": caps["distributor"],
            "u": "min(1, out_deg / 20)",
        },
        "consolidator": {
            "in_deg_min": 3,
            "support_cap": caps["consolidator"],
            "u": "min(1, in_deg / 6)",
        },
        "transit": {
            "is_seed": False,
            "depth_max_exclusive": 4,
            "pass_through_min": 0.8,
            "pass_through_max": 1.2,
            "support_cap": caps["transit"],
            "u": "max(0, 1 - abs(pass_through - 1) / 0.2)",
        },
        "terminal": {
            "is_seed": False,
            "depth_max_exclusive": 4,
            "in_tiyn_min_exclusive": 0,
            "out_deg": 0,
            "days_after_last_in_min": 2,
            "support_cap": caps["terminal"],
            "u": "min(1, days_after_last_in / 7)",
        },
        "support_formula": "cap * (0.5 + 0.5 * u)",
    }

    primary_roles = []
    role_scores = []
    all_matches = []

    def add_match(matches, role, u, reason):
        support = caps[role] * (0.5 + 0.5 * min(1.0, max(0.0, float(u))))
        matches.append({"role": role, "support": float(support), "reason": reason})

    for row in df.itertuples(index=False):
        matches = []
        seed_reach = int(row.seed_reach_count)
        betweenness = float(row.betweenness) if pd.notna(row.betweenness) else np.nan
        in_deg = int(row.in_deg)
        out_deg = int(row.out_deg)
        in_tiyn = int(row.in_tiyn)
        out_tiyn = int(row.out_tiyn)
        depth = int(row.depth)
        is_seed = bool(row.is_seed)

        if (
            q90_positive is not None
            and seed_reach >= 2
            and in_deg >= 2
            and out_deg >= 2
            and np.isfinite(betweenness)
            and betweenness > 0
            and betweenness >= q90_positive
        ):
            reason = (
                f"S={seed_reach}≥2; in_deg={in_deg}≥2; out_deg={out_deg}≥2; "
                f"B={_display_number(betweenness)}>0 и ≥Q90={_display_number(q90_positive)}"
            )
            add_match(matches, "coordinator", min(1.0, seed_reach / 4.0), reason)

        if out_deg >= 10:
            add_match(
                matches,
                "distributor",
                min(1.0, out_deg / 20.0),
                f"out_deg={out_deg}≥10",
            )

        if in_deg >= 3:
            add_match(
                matches,
                "consolidator",
                min(1.0, in_deg / 6.0),
                f"in_deg={in_deg}≥3",
            )

        ratio = row.pass_through
        if (
            not is_seed
            and depth < 4
            and in_tiyn > 0
            and out_tiyn > 0
            and pd.notna(ratio)
            and np.isfinite(float(ratio))
            and 0.8 <= float(ratio) <= 1.2
        ):
            u = max(0.0, 1.0 - abs(float(ratio) - 1.0) / 0.2)
            reason = (
                f"seed=нет; depth={depth}<4; in_tiyn={in_tiyn}>0; out_tiyn={out_tiyn}>0; "
                f"r={_display_number(ratio)}∈[0.8,1.2]"
            )
            add_match(matches, "transit", u, reason)

        days_after = row.days_after_last_in
        if (
            not is_seed
            and depth < 4
            and in_tiyn > 0
            and out_deg == 0
            and pd.notna(days_after)
            and int(days_after) >= 2
        ):
            days_after = int(days_after)
            reason = (
                f"seed=нет; depth={depth}<4; in_tiyn={in_tiyn}>0; out_deg=0; D={days_after}≥2"
            )
            add_match(matches, "terminal", min(1.0, days_after / 7.0), reason)

        if matches:
            primary_roles.append(matches[0]["role"])
            role_scores.append(matches[0]["support"])
            all_matches.append(matches)
        else:
            primary_roles.append("peripheral")
            role_scores.append(0.0)
            all_matches.append([])

    result = df.copy()
    result["role"] = primary_roles
    result["role_score"] = np.asarray(role_scores, dtype=np.float64)
    result["matched_roles"] = all_matches
    return result, role_parameters


def compute_priority(df: pd.DataFrame):
    """Score nodes from normalized role, volume, reach, and betweenness signals."""
    required = [
        "gid", "role", "role_score", "matched_roles", "in_tiyn", "out_tiyn",
        "seed_reach_count", "betweenness", "boundary", "isolated", "is_seed",
    ]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"priority calculation is missing required features: {', '.join(missing)}")

    weights = {"M": 0.35, "A": 0.30, "C": 0.20, "H": 0.15}
    volumes_kzt = np.asarray(
        [(int(in_tiyn) + int(out_tiyn)) / 100.0 for in_tiyn, out_tiyn in zip(df["in_tiyn"], df["out_tiyn"])],
        dtype=np.float64,
    )
    if not np.isfinite(volumes_kzt).all() or (volumes_kzt < 0).any():
        raise ValueError("node volumes must be finite and non-negative")

    positive_volumes = volumes_kzt[volumes_kzt > 0]
    q95_volume_kzt = (
        float(np.quantile(positive_volumes, 0.95, method="linear"))
        if len(positive_volumes)
        else None
    )
    betweenness_values = pd.to_numeric(df["betweenness"], errors="coerce").to_numpy(dtype=np.float64)
    if not np.isfinite(betweenness_values).all() or (betweenness_values < 0).any():
        raise ValueError("betweenness must contain finite non-negative values")
    positive_betweenness = betweenness_values[betweenness_values > 0]
    q95_betweenness = (
        float(np.quantile(positive_betweenness, 0.95, method="linear"))
        if len(positive_betweenness)
        else None
    )

    role_strength = []
    max_support_matches = []
    for matches in df["matched_roles"]:
        supports = []
        for match in matches or []:
            support = float(match["support"])
            if not np.isfinite(support) or not 0 <= support <= 1:
                raise ValueError("matched role support must be finite and within [0, 1]")
            supports.append(support)
        role_strength.append(max(supports, default=0.0))
        max_support_matches.append(
            max(matches, key=lambda match: float(match["support"])) if matches else None
        )
    M = np.asarray(role_strength, dtype=np.float64)

    if q95_volume_kzt is None or q95_volume_kzt <= 0:
        A = np.zeros(len(df), dtype=np.float64)
    else:
        denominator = float(np.log1p(q95_volume_kzt))
        A = np.clip(np.log1p(volumes_kzt) / denominator, 0.0, 1.0)

    reach = pd.to_numeric(df["seed_reach_count"], errors="raise").to_numpy(dtype=np.float64)
    if not np.isfinite(reach).all() or (reach < 0).any():
        raise ValueError("seed_reach_count must contain finite non-negative values")
    C = np.clip(reach / 5.0, 0.0, 1.0)

    if q95_betweenness is None or q95_betweenness <= 0:
        H = np.zeros(len(df), dtype=np.float64)
    else:
        H = np.clip(betweenness_values / q95_betweenness, 0.0, 1.0)

    components = {"M": M, "A": A, "C": C, "H": H}
    priority = np.clip(
        weights["M"] * M + weights["A"] * A + weights["C"] * C + weights["H"] * H,
        0.0,
        1.0,
    )

    evidence = []
    why = []
    component_rows = []
    for position, row in enumerate(df.itertuples(index=False)):
        values = {key: float(components[key][position]) for key in ("M", "A", "C", "H")}
        component_rows.append(values)
        gid = int(row.gid)
        volume = float(volumes_kzt[position])
        reach_count = int(row.seed_reach_count)
        betweenness = float(betweenness_values[position])
        limitation = _limitation(row)

        if row.role == "peripheral":
            out_deg = int(row.out_deg)
            in_deg = int(row.in_deg)
            in_volume = int(row.in_tiyn) / 100.0
            out_volume = int(row.out_tiyn) / 100.0
            evidence_text = (
                f"Совпадений правил: 0; in_deg={in_deg}, out_deg={out_deg}; "
                f"вход={in_volume:.2f}, выход={out_volume:.2f} KZT. "
                f"Роль не установлена; безопасность не оценена. {limitation}."
            )
        else:
            primary_match = next(
                (match for match in (row.matched_roles or []) if match["role"] == row.role),
                None,
            )
            if primary_match is None:
                raise ValueError(f"gid {gid} primary role is absent from matched_roles")
            evidence_text = (
                f"{row.role}: {primary_match['reason']}. {ROLE_EVIDENCE_CAVEATS[row.role]}. "
                f"Ограничение: {limitation}."
            )
        if len(evidence_text) > 200:
            raise ValueError(f"evidence for gid {gid} exceeds 200 characters")
        evidence.append(evidence_text)

        max_match = max_support_matches[position]
        support_role = max_match["role"] if max_match else "нет совпавших ролей"
        support_value = float(max_match["support"]) if max_match else 0.0
        primary_note = f"; основная={row.role}" if support_role != row.role else ""
        q95_volume_text = _display_number(q95_volume_kzt)
        q95_betweenness_text = _display_number(q95_betweenness)
        component_details = {
            "M": (
                f"M={_display_number(values['M'])}, max support {support_role}="
                f"{_display_number(support_value)}{primary_note}"
            ),
            "A": (
                f"A={_display_number(values['A'])}, V={volume:.2f} KZT, "
                f"Q95(V>0)={q95_volume_text} KZT"
            ),
            "C": f"C={_display_number(values['C'])}, S={reach_count}/5",
            "H": (
                f"H={_display_number(values['H'])}, B={_display_number(betweenness)}, "
                f"Q95(B>0)={q95_betweenness_text}"
            ),
        }
        contributions = sorted(
            ((weights[key] * values[key], key) for key in ("M", "A", "C", "H")),
            key=lambda item: (-item[0], ("M", "A", "C", "H").index(item[1])),
        )[:2]
        contribution_text = [
            (
                f"{component_details[key]}; вес={weights[key]:.2f}, "
                f"вклад={weights[key] * values[key]:.6f}"
            )
            for _weighted_value, key in contributions
        ]
        why_text = (
            f"P={priority[position]:.6f}; ведущие вклады: {contribution_text[0]}; "
            f"{contribution_text[1]}. Ограничение: {limitation}."
        )
        why.append(why_text)

    result = df.copy()
    result["priority_score"] = priority
    result["score_components"] = component_rows
    result["evidence"] = evidence
    result["why"] = why
    result = result.sort_values(
        ["priority_score", "gid"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)

    priority_parameters = {
        "rule_version": "1.0",
        "explanation_version": "1.0",
        "weights": weights,
        "volume_definition": "(in_tiyn + out_tiyn) / 100, in KZT",
        "volume_quantile": 0.95,
        "volume_quantile_method": "linear",
        "volume_q95_kzt": q95_volume_kzt,
        "component_formulas": {
            "M": "max(matched_roles.support), or 0 when there are no matches",
            "A": "min(1, log1p(V_kzt) / log1p(Q95(V_kzt > 0)))",
            "C": "min(1, seed_reach_count / 5)",
            "H": "min(1, betweenness / Q95(betweenness > 0))",
        },
        "betweenness_quantile": 0.95,
        "betweenness_quantile_method": "linear",
        "betweenness_q95_positive": q95_betweenness,
        "priority_formula": "0.35*M + 0.30*A + 0.20*C + 0.15*H",
        "sort_order": ["priority_score desc (unrounded)", "numeric gid asc"],
    }
    return result, priority_parameters
