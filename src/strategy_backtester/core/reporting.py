from typing import Any, Dict, List

import pandas as pd

from .metrics import equity_metrics, group_trade_metrics


REJECTION_COLUMNS = [
    "signals_seen",
    "accepted",
    "rejected_risk_budget",
    "rejected_max_positions",
    "rejected_liquidity",
    "rejected_regime",
    "rejected_regime_usable",
    "rejected_regime_trend",
    "rejected_regime_vol",
    "rejected_regime_prob",
    "rejected_regime_stability",
    "rejected_regime_flip_avoid",
    "rejected_cooldown",
    "rejected_min_notional",
    "rejected_event_calendar",
    "rejected_other",
]


def build_rejections_df(rejection_stats: Dict[pd.Timestamp, Dict[str, Dict[str, int]]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for date, books in rejection_stats.items():
        for book, counts in books.items():
            row = {"date": date, "book": book}
            for col in REJECTION_COLUMNS:
                row[col] = counts.get(col, 0)
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["date", "book"]).reset_index(drop=True)


def build_positions_df(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty or "position_id" not in trades_df.columns:
        return pd.DataFrame()
    df = trades_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    entry_actions = {"BUY", "SELL_SHORT"}
    add_actions = {"BUY_ADD", "SELL_SHORT_ADD"}
    exit_actions = {"SELL", "BUY_TO_COVER"}

    rows: List[Dict[str, Any]] = []
    for position_id, group in df.groupby("position_id"):
        entries = group[group["action"].isin(entry_actions)].sort_values("date")
        exits = group[group["action"].isin(exit_actions)].sort_values("date")
        if entries.empty or exits.empty:
            continue
        entry = entries.iloc[0]
        exit_row = exits.iloc[-1]
        adds = group[group["action"].isin(add_actions)]

        add_qty = (adds.get("qty", 0.0) * adds.get("multiplier", 1.0)).sum() if not adds.empty else 0.0
        add_notional = adds.get("trade_notional", 0.0).sum() if not adds.empty else 0.0
        avg_add_price = (add_notional / add_qty) if add_qty else None

        entry_notional = entry.get("trade_notional", 0.0) or 0.0
        total_entry_notional = entry_notional + add_notional
        realized_pnl = exits.get("pnl", 0.0).sum()
        return_pct = (realized_pnl / total_entry_notional * 100) if total_entry_notional else None

        commission_total = group["commission"].sum() if "commission" in group.columns else 0.0
        slippage_total = group["slippage_cost"].sum() if "slippage_cost" in group.columns else 0.0
        entry_date = entry.get("date")
        exit_date = exit_row.get("date")
        holding_days = exit_row.get("holding_days")
        if holding_days is None and entry_date is not None and exit_date is not None:
            holding_days = (exit_date - entry_date).days
        rows.append({
            "position_id": position_id,
            "entry_id": entry.get("entry_id"),
            "book": entry.get("book"),
            "strategy": entry.get("strategy"),
            "symbol": entry.get("symbol"),
            "entry_date": entry_date,
            "exit_date": exit_date,
            "entry_price": entry.get("price"),
            "exit_price": exit_row.get("price"),
            "holding_days": holding_days,
            "initial_risk": entry.get("initial_risk"),
            "adds_taken": len(adds),
            "avg_add_price": avg_add_price,
            "mfe_r": exit_row.get("mfe_r"),
            "mae_r": exit_row.get("mae_r"),
            "peak_r": exit_row.get("peak_r"),
            "exit_r": exit_row.get("exit_r"),
            "giveback_r": exit_row.get("giveback_r"),
            "exit_reason": exit_row.get("reason"),
            "stop_type_at_exit": exit_row.get("stop_type_at_exit"),
            "stop_level": exit_row.get("stop_level"),
            "stop_fill_price": exit_row.get("stop_fill_price"),
            "stop_fill_mode": exit_row.get("stop_fill_mode"),
            "gap_through_r": exit_row.get("gap_through_r"),
            "realized_pnl": realized_pnl,
            "return_pct": return_pct,
            "costs_commission": commission_total,
            "costs_slippage": slippage_total,
            "allocator_state_at_entry": entry.get("allocator_state"),
            "allocator_state_at_exit": exit_row.get("allocator_state"),
            "risk_budget_pct_at_entry": entry.get("book_risk_budget_pct"),
            "risk_budget_dollars_at_entry": entry.get("book_risk_budget_dollars"),
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["entry_date", "position_id"]).reset_index(drop=True)


def build_book_metrics(book_daily_df: pd.DataFrame, trades_df: pd.DataFrame) -> pd.DataFrame:
    if book_daily_df.empty:
        return pd.DataFrame()
    equity_cols = [
        col for col in book_daily_df.columns
        if col.startswith("equity_")
        and col != "equity_total"
        and not col.startswith("equity_dollars_")
    ]
    rows: List[Dict[str, Any]] = []
    for col in equity_cols:
        book = col.replace("equity_", "", 1)
        equity_df = book_daily_df[["date", col]].rename(columns={col: "equity"})
        book_trades = trades_df[trades_df.get("book") == book] if "book" in trades_df.columns else trades_df
        summary = equity_metrics(equity_df, book_trades)
        if summary:
            summary["book"] = book
            rows.append(summary)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("book").reset_index(drop=True)


def build_strategy_metrics_by_book(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty or "book" not in trades_df.columns:
        return pd.DataFrame()
    rows = []
    df = trades_df.copy()
    df["book"] = df["book"].fillna("unassigned")
    for book, df_book in df.groupby("book"):
        by_strategy = group_trade_metrics(df_book, "strategy")
        if by_strategy.empty:
            continue
        by_strategy.insert(0, "book", book)
        rows.append(by_strategy)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def build_exit_reason_attribution(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame()
    sells = trades_df[trades_df["action"].isin(["SELL", "BUY_TO_COVER"])].copy()
    if sells.empty:
        return pd.DataFrame()
    if "book" not in sells.columns:
        sells["book"] = "unassigned"
    else:
        sells["book"] = sells["book"].fillna("unassigned")
    sells["stop_type_at_exit"] = sells.get("stop_type_at_exit").fillna("none")
    group_cols = ["book", "reason", "stop_type_at_exit"]
    if "strategy" in sells.columns:
        group_cols.insert(1, "strategy")
    grouped_rows = []
    for group_keys, df in sells.groupby(group_cols):
        if "strategy" in group_cols:
            book, strategy, reason, stop_type = group_keys
        else:
            book, reason, stop_type = group_keys
            strategy = None
        win_rate = (df["pnl"] > 0).mean() * 100 if len(df) else 0.0
        row = {
            "book": book,
            "reason": reason,
            "stop_type_at_exit": stop_type,
            "count": len(df),
            "win_rate_pct": win_rate,
            "total_pnl": df["pnl"].sum(),
            "avg_exit_r": df.get("exit_r", pd.Series(dtype=float)).mean(),
            "avg_peak_r": df.get("peak_r", pd.Series(dtype=float)).mean(),
            "avg_giveback_r": df.get("giveback_r", pd.Series(dtype=float)).mean(),
        }
        if strategy is not None:
            row["strategy"] = strategy
        grouped_rows.append(row)
    out = pd.DataFrame(grouped_rows)
    if out.empty:
        return out
    sort_cols = ["book", "total_pnl"]
    if "strategy" in out.columns:
        sort_cols.insert(1, "strategy")
    return out.sort_values(sort_cols, ascending=[True] * (len(sort_cols) - 1) + [False]).reset_index(drop=True)


def build_regime_attribution(trades_df: pd.DataFrame, equity_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame()
    sells = trades_df[trades_df["action"].isin(["SELL", "BUY_TO_COVER"])].copy()
    if sells.empty:
        return pd.DataFrame()

    def _trade_bucket(series: pd.Series, default: str = "unknown") -> pd.Series:
        return series.fillna(default).astype(str)

    def _trade_stats(df: pd.DataFrame) -> Dict[str, Any]:
        if df.empty:
            return {
                "trades": 0,
                "win_rate_pct": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "avg_return_pct": 0.0,
            }
        win_rate = (df["pnl"] > 0).mean() * 100 if "pnl" in df.columns else 0.0
        return {
            "trades": len(df),
            "win_rate_pct": win_rate,
            "total_pnl": df.get("pnl", pd.Series(dtype=float)).sum(),
            "avg_pnl": df.get("pnl", pd.Series(dtype=float)).mean(),
            "avg_return_pct": df.get("return_pct", pd.Series(dtype=float)).mean(),
        }

    equity_df = equity_df.copy()
    if not equity_df.empty:
        equity_df["regime_trend_state"] = _trade_bucket(equity_df.get("regime_trend_state"))
        equity_df["regime_vol_state"] = _trade_bucket(equity_df.get("regime_vol_state"))
    prob_bins = [-float("inf"), 0.4, 0.6, 0.8, float("inf")]
    prob_labels = ["0-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]

    sells["entry_trend_state"] = _trade_bucket(sells.get("entry_trend_state"))
    sells["entry_vol_state"] = _trade_bucket(sells.get("entry_vol_state"))
    entry_prob = pd.to_numeric(sells.get("entry_risk_on_prob"), errors="coerce")
    sells["entry_prob_bucket"] = pd.cut(entry_prob, bins=prob_bins, labels=prob_labels, right=False)
    if not equity_df.empty:
        equity_prob = pd.to_numeric(equity_df.get("regime_risk_on_prob"), errors="coerce")
        equity_df["regime_prob_bucket"] = pd.cut(equity_prob, bins=prob_bins, labels=prob_labels, right=False)

    rows = []
    for bucket_type, trade_col, equity_col in [
        ("trend_state", "entry_trend_state", "regime_trend_state"),
        ("vol_state", "entry_vol_state", "regime_vol_state"),
        ("risk_on_prob_bucket", "entry_prob_bucket", "regime_prob_bucket"),
    ]:
        for bucket, df in sells.groupby(trade_col, dropna=False):
            stats = _trade_stats(df)
            row = {"bucket_type": bucket_type, "bucket": bucket}
            row.update(stats)
            if not equity_df.empty and equity_col in equity_df.columns:
                eq_slice = equity_df[equity_df[equity_col] == bucket]
                if not eq_slice.empty:
                    invested_pct = (eq_slice["invested"] / eq_slice["equity"]).replace([float("inf"), -float("inf")], pd.NA)
                    row["time_in_market_pct"] = (eq_slice["positions"] > 0).mean() * 100
                    row["avg_invested_pct"] = invested_pct.dropna().mean() * 100 if not invested_pct.dropna().empty else 0.0
                else:
                    row["time_in_market_pct"] = 0.0
                    row["avg_invested_pct"] = 0.0
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def build_book_daily_attribution(book_daily_df: pd.DataFrame) -> pd.DataFrame:
    if book_daily_df.empty:
        return pd.DataFrame()
    books = [
        col.replace("equity_", "", 1)
        for col in book_daily_df.columns
        if col.startswith("equity_")
        and col != "equity_total"
        and not col.startswith("equity_dollars_")
    ]
    rows: List[Dict[str, Any]] = []
    for book in books:
        index_col = f"equity_{book}"
        pnl_col = f"pnl_total_{book}"
        drawdown_col = f"book_drawdown_pct_{book}"
        reference_col = f"risk_budget_reference_{book}"
        open_risk_col = f"open_risk_dollars_{book}"
        gross_exposure_col = f"gross_exposure_{book}"
        state_col = f"allocator_state_{book}"
        if index_col not in book_daily_df.columns:
            continue
        df = book_daily_df[["date", index_col]].copy()
        df["book_index_raw"] = pd.to_numeric(book_daily_df.get(f"book_index_raw_{book}"), errors="coerce")
        df["book_index_for_alloc"] = pd.to_numeric(book_daily_df.get(f"book_index_alloc_{book}"), errors="coerce")
        df["pnl_total"] = pd.to_numeric(book_daily_df.get(pnl_col), errors="coerce")
        df["book_drawdown_pct"] = pd.to_numeric(book_daily_df.get(drawdown_col), errors="coerce")
        df["book_drawdown_alloc_pct"] = pd.to_numeric(
            book_daily_df.get(f"book_drawdown_alloc_pct_{book}"),
            errors="coerce",
        )
        df["risk_budget_reference"] = pd.to_numeric(book_daily_df.get(reference_col), errors="coerce")
        df["open_risk_dollars"] = pd.to_numeric(book_daily_df.get(open_risk_col), errors="coerce")
        df["gross_exposure"] = pd.to_numeric(book_daily_df.get(gross_exposure_col), errors="coerce")
        df["allocator_state"] = book_daily_df.get(state_col)
        df["book"] = book
        df["daily_pnl"] = df["pnl_total"].diff().fillna(df["pnl_total"])
        reference = df["risk_budget_reference"].replace(0, pd.NA)
        df["daily_return_on_risk_budget"] = df["daily_pnl"] / reference
        df.rename(columns={index_col: "book_index"}, inplace=True)
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    return out[
        [
            "date",
            "book",
            "daily_pnl",
            "daily_return_on_risk_budget",
            "pnl_total",
            "book_index",
            "book_index_raw",
            "book_index_for_alloc",
            "book_drawdown_pct",
            "book_drawdown_alloc_pct",
            "open_risk_dollars",
            "gross_exposure",
            "allocator_state",
            "risk_budget_reference",
        ]
    ].sort_values(["date", "book"]).reset_index(drop=True)
