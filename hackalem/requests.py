"""Observed reasons to ask for more transaction data; no role or score changes."""

import pandas as pd

from .clusters import _format_kzt


def build_data_requests(df: pd.DataFrame, daily_profiles_by_gid: dict[str, list[dict]]) -> pd.DataFrame:
    """Attach ordered, human-readable next-data requests for every exact gid."""
    required = {"gid", "depth", "in_tiyn", "out_tiyn", "in_deg", "out_deg", "days_after_last_in", "last_in"}
    missing = required - set(df)
    if missing:
        raise ValueError(f"next-data requests require columns: {', '.join(sorted(missing))}")
    gids = {str(int(gid)) for gid in df["gid"]}
    if not isinstance(daily_profiles_by_gid, dict) or set(daily_profiles_by_gid) != gids:
        raise ValueError("daily profiles must cover every exact gid before data requests")

    requests = []
    for row in df.itertuples(index=False):
        gid = str(int(row.gid))
        items = []
        if int(row.depth) == 4:
            items.append({"reason_code": "boundary", "text":
                "Клиент находится на глубине 4: следующий исходящий слой в этой выгрузке неизвестен. "
                "Запросите его исходящие переводы с указанием банка, периода и порога выгрузки."})
        if int(row.in_tiyn) > 0 and int(row.out_deg) == 0 and pd.notna(row.days_after_last_in) and int(row.days_after_last_in) < 2:
            last_in = pd.Timestamp(row.last_in).date().isoformat()
            items.append({"reason_code": "short_followup", "text":
                f"Последнее поступление наблюдалось {last_in}, после него прошло менее двух дней наблюдения. "
                "Запросите данные минимум до двух дней после этой даты."})
        if int(row.in_tiyn) > 0 or int(row.out_tiyn) > 0:
            items.append({"reason_code": "incomplete_balance", "text":
                f"В выборке видны входящие {_format_kzt(int(row.in_tiyn))} KZT и исходящие "
                f"{_format_kzt(int(row.out_tiyn))} KZT. Запросите полный вход и начальный и конечный остатки "
                "для проверки баланса: эти суммы ограничены условиями выгрузки."})
        profile = daily_profiles_by_gid[gid]
        if not isinstance(profile, list):
            raise ValueError(f"daily profile for gid={gid} must be a list")
        same_day = sorted(item["date"] for item in profile if item["in_tx"] > 0 and item["out_tx"] > 0)
        if same_day:
            items.append({"reason_code": "same_day_order", "text":
                f"{same_day[0]} есть входящие и исходящие операции в один день. "
                "Запросите временные метки переводов, чтобы проверить их порядок внутри дня."})
        if int(row.in_deg) == 0 and int(row.out_deg) == 0:
            items.append({"reason_code": "isolated", "text":
                "Для этого клиента в выборке нет входящих и исходящих связей. "
                "Уточните покрытие данных по ID, включая операции вне банка, периода или порога; "
                "отсутствие записей не объясняет причину."})
        requests.append(items)

    result = df.copy()
    result["next_data_requests"] = requests
    return result
