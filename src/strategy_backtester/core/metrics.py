from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd


def max_consecutive_losses(sells_df: pd.DataFrame) -> int:
    if sells_df.empty or "pnl" not in sells_df.columns:
        return 0
    loss_flags = (sells_df["pnl"] <= 0).astype(int).tolist()
    max_run = 0
    current = 0
    for flag in loss_flags:
        if flag:
            current += 1
            if current > max_run:
                max_run = current
        else:
            current = 0
    return max_run


def trade_summary(trades_df: pd.DataFrame) -> Dict[str, Any]:
    sells = trades_df[trades_df["action"].isin(["SELL", "BUY_TO_COVER"])] if not trades_df.empty else pd.DataFrame()
    if sells.empty:
        return {
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "avg_pnl": 0.0,
            "total_pnl": 0.0,
            "expectancy": 0.0,
            "payoff_ratio": 0.0,
            "max_consecutive_losses": 0,
            "avg_commission": 0.0,
            "total_commission": 0.0,
            "avg_slippage_cost": 0.0,
            "total_slippage_cost": 0.0,
        }

    wins = sells[sells["pnl"] > 0]
    losses = sells[sells["pnl"] <= 0]
    win_rate = len(wins) / len(sells) if len(sells) else 0.0
    avg_win = wins["pnl"].mean() if not wins.empty else 0.0
    avg_loss = losses["pnl"].mean() if not losses.empty else 0.0
    profit_factor = wins["pnl"].sum() / abs(losses["pnl"].sum()) if not losses.empty and losses["pnl"].sum() != 0 else 0.0
    avg_hold = sells["holding_days"].mean() if "holding_days" in sells.columns else 0.0
    avg_return = sells["return_pct"].mean() if "return_pct" in sells.columns else 0.0
    payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss else 0.0
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
    max_consec_losses = max_consecutive_losses(sells)
    total_commission = trades_df["commission"].sum() if "commission" in trades_df.columns else 0.0
    total_slippage = trades_df["slippage_cost"].sum() if "slippage_cost" in trades_df.columns else 0.0
    avg_commission = trades_df["commission"].mean() if "commission" in trades_df.columns else 0.0
    avg_slippage = trades_df["slippage_cost"].mean() if "slippage_cost" in trades_df.columns else 0.0

    return {
        "total_trades": len(sells),
        "win_rate_pct": win_rate * 100,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "avg_holding_days": avg_hold,
        "avg_trade_return_pct": avg_return,
        "payoff_ratio": payoff_ratio,
        "expectancy": expectancy,
        "max_consecutive_losses": max_consec_losses,
        "avg_pnl": sells["pnl"].mean(),
        "total_pnl": sells["pnl"].sum(),
        "avg_commission": avg_commission,
        "total_commission": total_commission,
        "avg_slippage_cost": avg_slippage,
        "total_slippage_cost": total_slippage,
    }


def _time_to_recovery_days(equity: pd.Series) -> Tuple[int, bool]:
    if equity.empty:
        return 0, False
    peak_value = float(equity.iloc[0])
    peak_date = equity.index[0]
    in_drawdown = False
    max_days = 0
    last_date = peak_date
    for date, value in equity.items():
        last_date = date
        value = float(value)
        if value >= peak_value:
            if in_drawdown:
                dd_days = (date - peak_date).days
                if dd_days > max_days:
                    max_days = dd_days
                in_drawdown = False
            peak_value = value
            peak_date = date
        else:
            in_drawdown = True
    open_drawdown = False
    if in_drawdown:
        dd_days = (last_date - peak_date).days
        if dd_days > max_days:
            max_days = dd_days
        open_drawdown = True
    return int(max_days), open_drawdown


def equity_metrics(equity_df: pd.DataFrame, trades_df: pd.DataFrame) -> Dict[str, Any]:
    if equity_df.empty:
        return {}

    equity_df = equity_df.copy()
    if "date" not in equity_df.columns:
        equity_df["date"] = equity_df.index
        equity_df.index = pd.RangeIndex(len(equity_df))
    else:
        index_names = list(equity_df.index.names) if hasattr(equity_df.index, "names") else []
        if equity_df.index.name == "date" or "date" in index_names:
            equity_df.index = pd.RangeIndex(len(equity_df))
    equity_df["date"] = pd.to_datetime(equity_df["date"])
    equity_df["equity"] = pd.to_numeric(equity_df["equity"], errors="coerce")
    equity_df["equity"] = equity_df["equity"].replace([np.inf, -np.inf], np.nan)
    equity_df = equity_df.dropna(subset=["equity"])
    if equity_df.empty:
        return {}
    equity_df = equity_df.sort_values("date")
    equity_df.set_index("date", inplace=True)
    start_eq = equity_df.iloc[0]["equity"]
    end_eq = equity_df.iloc[-1]["equity"]
    total_return = (end_eq / start_eq) - 1 if start_eq else 0.0
    days = (equity_df.index[-1] - equity_df.index[0]).days
    if days > 0 and start_eq and start_eq > 0 and end_eq > 0:
        annualized_return = (end_eq / start_eq) ** (365.25 / days) - 1
    else:
        annualized_return = 0.0

    daily_returns = equity_df["equity"].pct_change()
    daily_returns = daily_returns.replace([np.inf, -np.inf], np.nan).dropna()
    if len(daily_returns) > 1:
        annualized_vol = daily_returns.std() * (252 ** 0.5)
        sharpe = (daily_returns.mean() * 252) / annualized_vol if annualized_vol else 0.0
    else:
        annualized_vol = 0.0
        sharpe = 0.0

    drawdown = (equity_df["equity"] / equity_df["equity"].cummax()) - 1.0
    max_drawdown = drawdown.min() if not drawdown.empty else 0.0
    ulcer_index = (drawdown.pow(2).mean() ** 0.5) if not drawdown.empty else 0.0
    time_to_recovery_days, time_to_recovery_open = _time_to_recovery_days(equity_df["equity"])

    downside = daily_returns[daily_returns < 0]
    downside_dev = downside.std() * (252 ** 0.5) if len(downside) > 1 else 0.0
    sortino = (daily_returns.mean() * 252) / downside_dev if downside_dev else 0.0
    calmar = (annualized_return / abs(max_drawdown)) if max_drawdown else 0.0

    rolling_window = 126  # ~6 months of trading days
    if len(daily_returns) >= rolling_window:
        roll_mean = daily_returns.rolling(rolling_window).mean()
        roll_std = daily_returns.rolling(rolling_window).std()
        rolling_sharpe = (roll_mean * 252) / (roll_std * (252 ** 0.5))
        rolling_sharpe = rolling_sharpe.dropna()
        rolling_return = (1 + daily_returns).rolling(rolling_window).apply(lambda x: x.prod() - 1, raw=True)
        rolling_return = rolling_return.dropna()
        rolling_sharpe_min = rolling_sharpe.min()
        rolling_sharpe_mean = rolling_sharpe.mean()
        rolling_return_min = rolling_return.min()
        rolling_return_mean = rolling_return.mean()
    else:
        rolling_sharpe_min = 0.0
        rolling_sharpe_mean = 0.0
        rolling_return_min = 0.0
        rolling_return_mean = 0.0

    monthly = equity_df["equity"].resample("ME").last().pct_change().dropna()
    quarterly = equity_df["equity"].resample("QE").last().pct_change().dropna()
    worst_month = monthly.min() if not monthly.empty else 0.0
    worst_quarter = quarterly.min() if not quarterly.empty else 0.0

    skew = daily_returns.skew() if len(daily_returns) > 2 else 0.0
    kurtosis = daily_returns.kurtosis() if len(daily_returns) > 3 else 0.0

    time_in_market = 0.0
    if "positions" in equity_df.columns:
        time_in_market = (equity_df["positions"] > 0).mean() * 100

    turnover_pct = 0.0
    turnover_margin_pct = 0.0
    if "trade_notional" in trades_df.columns and not trades_df.empty:
        avg_equity = equity_df["equity"].mean()
        if avg_equity:
            trade_notional = pd.to_numeric(trades_df["trade_notional"], errors="coerce").abs()
            turnover_pct = (trade_notional.sum() / avg_equity) * 100
            if "margin_pct" in trades_df.columns:
                margin_pct = pd.to_numeric(trades_df["margin_pct"], errors="coerce").fillna(1.0)
                turnover_margin_pct = ((trade_notional * margin_pct).sum() / avg_equity) * 100
            else:
                turnover_margin_pct = turnover_pct

    turnover_annualized_pct = 0.0
    turnover_margin_annualized_pct = 0.0
    trading_days = equity_df.index.normalize().nunique() if not equity_df.empty else 0
    if trading_days:
        factor = 252 / trading_days
        turnover_annualized_pct = turnover_pct * factor
        turnover_margin_annualized_pct = turnover_margin_pct * factor

    vol_scale_min = 0.0
    vol_scale_median = 0.0
    vol_scale_max = 0.0
    if "vol_scale" in equity_df.columns and not equity_df["vol_scale"].empty:
        vol_scale_min = float(equity_df["vol_scale"].min())
        vol_scale_median = float(equity_df["vol_scale"].median())
        vol_scale_max = float(equity_df["vol_scale"].max())

    invested_metrics: Dict[str, Any] = {}
    if "invested" in equity_df.columns and not equity_df["invested"].empty:
        invested = pd.to_numeric(equity_df["invested"], errors="coerce")
        equity = pd.to_numeric(equity_df["equity"], errors="coerce")
        invested_pct = (invested / equity.replace(0, pd.NA)) * 100
        invested_pct = invested_pct.dropna()
        invested_metrics = {
            "max_invested": float(invested.max()) if not invested.empty else 0.0,
            "max_invested_pct": float(invested_pct.max()) if not invested_pct.empty else 0.0,
            "invested_pct_p95": float(invested_pct.quantile(0.95)) if not invested_pct.empty else 0.0,
            "invested_pct_p99": float(invested_pct.quantile(0.99)) if not invested_pct.empty else 0.0,
            "invested_pct_median": float(invested_pct.median()) if not invested_pct.empty else 0.0,
        }

    summary = trade_summary(trades_df)

    return {
        "initial_equity": start_eq,
        "final_equity": end_eq,
        "total_return_pct": total_return * 100,
        "annualized_return_pct": annualized_return * 100,
        "annualized_volatility_pct": annualized_vol * 100,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "max_drawdown_pct": max_drawdown * 100,
        "time_to_recovery_days": time_to_recovery_days,
        "time_to_recovery_open": time_to_recovery_open,
        "ulcer_index": ulcer_index,
        "rolling_sharpe_min": rolling_sharpe_min,
        "rolling_sharpe_mean": rolling_sharpe_mean,
        "rolling_return_6m_min_pct": rolling_return_min * 100,
        "rolling_return_6m_mean_pct": rolling_return_mean * 100,
        "worst_month_pct": worst_month * 100,
        "worst_quarter_pct": worst_quarter * 100,
        "skewness": skew,
        "kurtosis": kurtosis,
        "time_in_market_pct": time_in_market,
        "turnover_pct": turnover_pct,
        "turnover_margin_pct": turnover_margin_pct,
        "turnover_annualized_pct": turnover_annualized_pct,
        "turnover_margin_annualized_pct": turnover_margin_annualized_pct,
        "vol_scale_min": vol_scale_min,
        "vol_scale_median": vol_scale_median,
        "vol_scale_max": vol_scale_max,
        **invested_metrics,
        **summary,
    }


def group_trade_metrics(trades_df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    if trades_df.empty or group_col not in trades_df.columns:
        return pd.DataFrame()
    sells = trades_df[trades_df["action"].isin(["SELL", "BUY_TO_COVER"])].copy()
    if sells.empty:
        return pd.DataFrame()

    grouped = []
    for group, df in sells.groupby(group_col):
        wins = df[df["pnl"] > 0]
        losses = df[df["pnl"] <= 0]
        win_rate = len(wins) / len(df) if len(df) else 0.0
        avg_win = wins["pnl"].mean() if not wins.empty else 0.0
        avg_loss = losses["pnl"].mean() if not losses.empty else 0.0
        profit_factor = wins["pnl"].sum() / abs(losses["pnl"].sum()) if not losses.empty and losses["pnl"].sum() != 0 else 0.0
        avg_hold = df["holding_days"].mean() if "holding_days" in df.columns else 0.0
        avg_return = df["return_pct"].mean() if "return_pct" in df.columns else 0.0
        compounded = (1 + (df["return_pct"] / 100)).prod() - 1 if "return_pct" in df.columns else 0.0
        if "entry_date" in df.columns and not df["entry_date"].isna().all():
            span_days = (df["date"].max() - df["entry_date"].min()).days
        else:
            span_days = 0
        annualized = (1 + compounded) ** (365.25 / span_days) - 1 if span_days > 0 else 0.0
        payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss else 0.0
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
        max_consec_losses = max_consecutive_losses(df)
        peak_r = pd.to_numeric(df.get("peak_r"), errors="coerce")
        exit_r = pd.to_numeric(df.get("exit_r"), errors="coerce")
        holding_days = pd.to_numeric(df.get("holding_days"), errors="coerce")
        peak_ge_1 = (peak_r >= 1.0).fillna(False)
        pct_peak_ge_1 = peak_ge_1.mean() * 100 if len(df) else 0.0
        if peak_ge_1.any():
            exit_ge_half = (exit_r[peak_ge_1] >= 0.5).fillna(False)
            pct_exit_ge_half_given = exit_ge_half.mean() * 100
        else:
            pct_exit_ge_half_given = 0.0
        if holding_days is not None and exit_r is not None:
            early_mask = (holding_days <= 5) & exit_r.notna()
            avg_exit_r_first_5 = exit_r[early_mask].mean() if early_mask.any() else 0.0
            early_loss_mask = early_mask & (exit_r < 0)
            avg_loss_r_first_5_losses_only = exit_r[early_loss_mask].mean() if early_loss_mask.any() else 0.0
        else:
            avg_exit_r_first_5 = 0.0
            avg_loss_r_first_5_losses_only = 0.0

        grouped.append({
            group_col: group,
            "trades": len(df),
            "win_rate_pct": win_rate * 100,
            "total_pnl": df["pnl"].sum(),
            "avg_pnl": df["pnl"].mean(),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "avg_holding_days": avg_hold,
            "avg_trade_return_pct": avg_return,
            "total_return_pct": compounded * 100,
            "annualized_return_pct": annualized * 100,
            "payoff_ratio": payoff_ratio,
            "expectancy": expectancy,
            "max_consecutive_losses": max_consec_losses,
            "pct_peak_r_ge_1": pct_peak_ge_1,
            "pct_exit_ge_0_5r_given_peak_1": pct_exit_ge_half_given,
            "avg_exit_r_first_5_bars": avg_exit_r_first_5,
            "avg_loss_r_first_5_bars_losses_only": avg_loss_r_first_5_losses_only,
        })

    return pd.DataFrame(grouped).sort_values(by="total_pnl", ascending=False).reset_index(drop=True)
