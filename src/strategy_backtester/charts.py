import logging
import os
import re
from typing import Dict, Iterable, Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


_ENTRY_ACTIONS = {"BUY", "SELL_SHORT"}
_EXIT_ACTIONS = {"SELL", "BUY_TO_COVER"}
_ADD_ACTIONS = {"BUY_ADD", "SELL_SHORT_ADD"}


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def _select_tickers(trades: pd.DataFrame, tickers: Optional[Iterable[str]]) -> list[str]:
    if tickers:
        return [t for t in tickers if t]
    if "symbol" not in trades.columns:
        return []
    return sorted(trades["symbol"].dropna().unique().tolist())


def _align_timestamp(ts: pd.Timestamp, index: pd.DatetimeIndex) -> pd.Timestamp:
    if index.tz is not None and ts.tz is None:
        return ts.tz_localize(index.tz)
    if index.tz is None and ts.tz is not None:
        return ts.tz_convert(None)
    return ts


def _index_position(index: pd.DatetimeIndex, ts: pd.Timestamp) -> Optional[int]:
    if index.empty:
        return None
    ts = _align_timestamp(ts, index)
    pos = index.get_indexer([ts], method="nearest")
    if pos.size and pos[0] >= 0:
        return int(pos[0])
    return None


def _plot_candles(ax, plot_df: pd.DataFrame, x: np.ndarray) -> None:
    import matplotlib.patches as patches

    open_px = plot_df["Open"].to_numpy()
    high_px = plot_df["High"].to_numpy()
    low_px = plot_df["Low"].to_numpy()
    close_px = plot_df["Close"].to_numpy()
    width = 0.6

    for i in range(len(plot_df)):
        color = "#2ca02c" if close_px[i] >= open_px[i] else "#d62728"
        ax.vlines(x[i], low_px[i], high_px[i], color=color, linewidth=1)
        lower = min(open_px[i], close_px[i])
        height = abs(close_px[i] - open_px[i])
        if height == 0:
            height = max((high_px[i] - low_px[i]) * 0.02, 0.01)
        rect = patches.Rectangle(
            (x[i] - width / 2, lower),
            width,
            height,
            facecolor=color,
            edgecolor=color,
            linewidth=0.8,
        )
        ax.add_patch(rect)
    ax.set_xlim(-1, len(plot_df))


def _plot_indicators(
    ax,
    plot_df: pd.DataFrame,
    x: np.ndarray,
    *,
    ema_periods: Sequence[int],
    sma_periods: Sequence[int],
) -> None:
    close = plot_df["Close"]
    for period in ema_periods:
        if period <= 1:
            continue
        ema = close.ewm(span=period, adjust=False).mean()
        ax.plot(x, ema, linewidth=1.1, label=f"EMA{period}")
    for period in sma_periods:
        if period <= 1:
            continue
        sma = close.rolling(period).mean()
        ax.plot(x, sma, linewidth=1.1, label=f"SMA{period}")
    _plot_vwap(ax, plot_df, x)


def _calc_vwap(plot_df: pd.DataFrame) -> pd.Series:
    if "Volume" not in plot_df.columns:
        return pd.Series(index=plot_df.index, dtype="float64")
    volume = plot_df["Volume"].fillna(0.0)
    typical = (plot_df["High"] + plot_df["Low"] + plot_df["Close"]) / 3.0
    pv = typical * volume
    if isinstance(plot_df.index, pd.DatetimeIndex):
        dates = plot_df.index.normalize()
        if dates.duplicated().any():
            cumsum_pv = pv.groupby(dates).cumsum()
            cumsum_vol = volume.groupby(dates).cumsum()
        else:
            cumsum_pv = pv.cumsum()
            cumsum_vol = volume.cumsum()
    else:
        cumsum_pv = pv.cumsum()
        cumsum_vol = volume.cumsum()
    vwap = cumsum_pv / cumsum_vol.replace(0, np.nan)
    return vwap


def _plot_vwap(ax, plot_df: pd.DataFrame, x: np.ndarray) -> None:
    vwap = _calc_vwap(plot_df)
    if vwap.empty or vwap.isna().all():
        return
    ax.plot(x, vwap, color="#9467bd", linewidth=1.1, label="VWAP")


def _plot_volume(ax, plot_df: pd.DataFrame, x: np.ndarray) -> None:
    volume = plot_df["Volume"].to_numpy()
    if volume.size == 0:
        return
    up = plot_df["Close"].to_numpy() >= plot_df["Open"].to_numpy()
    colors = np.where(up, "#2ca02c", "#d62728")
    ax.bar(x, volume, color=colors, width=0.6, alpha=0.5)
    ax.set_ylabel("Volume")


def _calc_rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(0.0)


def _plot_rsi(
    ax,
    plot_df: pd.DataFrame,
    x: np.ndarray,
    length: int,
    overbought: float,
    oversold: float,
) -> None:
    rsi = _calc_rsi(plot_df["Close"], length)
    ax.plot(x, rsi, color="#1f77b4", linewidth=1.0, label=f"RSI{length}")
    ax.axhline(overbought, color="#d62728", linestyle="--", linewidth=0.8)
    ax.axhline(oversold, color="#2ca02c", linestyle="--", linewidth=0.8)
    ax.set_ylim(0, 100)
    ax.set_ylabel("RSI")


def _plot_markers(ax, plot_df: pd.DataFrame, trades: pd.DataFrame) -> None:
    if trades.empty:
        return
    x_positions = []
    y_positions = []
    for _, row in trades.iterrows():
        ts = pd.Timestamp(row["date"])
        pos = _index_position(plot_df.index, ts)
        if pos is None:
            continue
        x_positions.append(pos)
        y_positions.append(row["price"])
    if x_positions:
        ax.scatter(x_positions, y_positions, **_trade_marker_style(trades))


def _trade_marker_style(trades: pd.DataFrame) -> dict:
    actions = set(trades["action"].unique())
    if actions & _ENTRY_ACTIONS:
        return {"marker": "^", "color": "#00A651", "s": 90, "label": "Entry", "zorder": 4}
    if actions & _EXIT_ACTIONS:
        if "partial" in trades.columns and trades["partial"].astype(bool).any():
            return {"marker": "v", "facecolors": "none", "edgecolors": "#FF8C00", "s": 90, "label": "Partial Exit", "zorder": 4}
        return {"marker": "v", "color": "#E23D28", "s": 90, "label": "Exit", "zorder": 4}
    if actions & _ADD_ACTIONS:
        return {"marker": "o", "color": "#6A5ACD", "s": 40, "label": "Add", "zorder": 4}
    return {"marker": "o", "color": "#7f7f7f", "s": 40, "label": "Trade", "zorder": 4}


def _finalize_axes(ax, plot_df: pd.DataFrame, title: str) -> None:
    ax.set_title(title)
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.2)
    ticks = np.linspace(0, len(plot_df) - 1, num=min(8, len(plot_df))).astype(int)
    tick_labels = [plot_df.index[i].strftime("%Y-%m-%d") for i in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(tick_labels, rotation=30, ha="right")


def _annotate_point(
    ax,
    plot_df: pd.DataFrame,
    date_value: pd.Timestamp,
    price: float,
    label: str,
    color: str,
    y_offset: float,
) -> None:
    pos = _index_position(plot_df.index, pd.Timestamp(date_value))
    if pos is None:
        return
    ax.annotate(
        label,
        xy=(pos, price),
        xytext=(pos, price + y_offset),
        textcoords="data",
        fontsize=9,
        color=color,
        ha="center",
        va="bottom",
        arrowprops=dict(
            arrowstyle="-|>",
            color=color,
            lw=1.5,
            shrinkA=0,
            shrinkB=2,
        ),
        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.8),
        zorder=5,
    )


def generate_trade_charts(
    trades: pd.DataFrame,
    data_by_ticker: Dict[str, pd.DataFrame],
    output_dir: str,
    *,
    tickers: Optional[Iterable[str]] = None,
    start_date: Optional[pd.Timestamp] = None,
    end_date: Optional[pd.Timestamp] = None,
    style: str = "close",
    figsize: Sequence[float] = (12, 6),
    file_format: str = "png",
    dpi: int = 120,
    include_adds: bool = True,
    include_partials: bool = True,
    ema_periods: Sequence[int] = (20, 50),
    sma_periods: Sequence[int] = (200,),
    include_volume: bool = False,
    include_rsi: bool = False,
    rsi_length: int = 14,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
) -> None:
    if trades is None or trades.empty:
        logger.info("Chart generation skipped (no trades).")
        return

    try:
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.warning("Chart generation skipped (matplotlib not available): %s", exc)
        return

    os.makedirs(output_dir, exist_ok=True)
    tickers_list = _select_tickers(trades, tickers)
    if not tickers_list:
        logger.info("Chart generation skipped (no tickers selected).")
        return

    trades = trades.copy()
    trades["date"] = pd.to_datetime(trades["date"], errors="coerce")
    trades = trades.dropna(subset=["date"])

    for ticker in tickers_list:
        df = data_by_ticker.get(ticker)
        if df is None or df.empty:
            logger.warning("Chart skipped for %s (missing price data).", ticker)
            continue

        plot_df = df
        if start_date is not None:
            plot_df = plot_df[plot_df.index >= start_date]
        if end_date is not None:
            plot_df = plot_df[plot_df.index <= end_date]
        if plot_df.empty:
            logger.warning("Chart skipped for %s (no data in range).", ticker)
            continue

        ticker_trades = trades[trades["symbol"] == ticker]
        if ticker_trades.empty:
            continue

        entries = ticker_trades[ticker_trades["action"].isin(_ENTRY_ACTIONS)]
        exits = ticker_trades[ticker_trades["action"].isin(_EXIT_ACTIONS)]
        adds = ticker_trades[ticker_trades["action"].isin(_ADD_ACTIONS)] if include_adds else pd.DataFrame()

        axes = []
        if include_volume and include_rsi:
            fig, axes = plt.subplots(
                3,
                1,
                figsize=figsize,
                dpi=dpi,
                gridspec_kw={"height_ratios": [3, 1, 1]},
                sharex=True,
            )
        elif include_volume or include_rsi:
            fig, axes = plt.subplots(
                2,
                1,
                figsize=figsize,
                dpi=dpi,
                gridspec_kw={"height_ratios": [3, 1]},
                sharex=True,
            )
        else:
            fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
            axes = [ax]
        ax = axes[0]
        ax_vol = None
        ax_rsi = None
        if include_volume and include_rsi:
            ax_vol = axes[1]
            ax_rsi = axes[2]
        elif include_volume:
            ax_vol = axes[1]
        elif include_rsi:
            ax_rsi = axes[1]
        x = np.arange(len(plot_df))
        if style == "candles":
            _plot_candles(ax, plot_df, x)
        else:
            ax.plot(x, plot_df["Close"], color="#1f77b4", linewidth=1.2, label="Close")
        _plot_indicators(ax, plot_df, x, ema_periods=ema_periods, sma_periods=sma_periods)
        if ax_vol is not None:
            _plot_volume(ax_vol, plot_df, x)
        if ax_rsi is not None:
            _plot_rsi(ax_rsi, plot_df, x, rsi_length, rsi_overbought, rsi_oversold)

        if not entries.empty:
            ax.scatter(
                entries["date"],
                entries["price"],
                marker="^",
                color="#00A651",
                s=90,
                label="Entry",
                zorder=3,
            )
        if not exits.empty:
            marker = "v"
            colors = "#E23D28"
            if include_partials and "partial" in exits.columns:
                partials = exits[exits["partial"].astype(bool)]
                fulls = exits[~exits["partial"].astype(bool)]
                if not fulls.empty:
                    ax.scatter(
                        fulls["date"],
                        fulls["price"],
                        marker=marker,
                        color=colors,
                        s=90,
                        label="Exit",
                        zorder=3,
                    )
                if not partials.empty:
                    ax.scatter(
                        partials["date"],
                        partials["price"],
                        marker=marker,
                        facecolors="none",
                        edgecolors="#FF8C00",
                        s=90,
                        label="Partial Exit",
                        zorder=3,
                    )
            else:
                ax.scatter(
                    exits["date"],
                    exits["price"],
                    marker=marker,
                    color=colors,
                    s=90,
                    label="Exit",
                    zorder=3,
                )

        if include_adds and not adds.empty:
            ax.scatter(
                adds["date"],
                adds["price"],
                marker="o",
                color="#6A5ACD",
                s=40,
                label="Add",
                zorder=3,
            )

        _finalize_axes(ax, plot_df, f"{ticker} - Entry/Exit Chart")
        ax.legend(loc="best", fontsize=8)
        if ax_vol is not None:
            ax_vol.grid(True, alpha=0.2)
        if ax_rsi is not None:
            ax_rsi.grid(True, alpha=0.2)

        safe_name = _safe_filename(ticker)
        output_path = os.path.join(output_dir, f"{safe_name}.{file_format}")
        fig.savefig(output_path, bbox_inches="tight")
        plt.close(fig)


def generate_position_charts(
    positions: pd.DataFrame,
    trades: pd.DataFrame,
    data_by_ticker: Dict[str, pd.DataFrame],
    output_dir: str,
    *,
    window_bars_before: int = 40,
    window_bars_after: int = 40,
    style: str = "candles",
    file_format: str = "png",
    dpi: int = 200,
    figsize: Sequence[float] = (14, 8),
    include_adds: bool = True,
    include_partials: bool = True,
    ema_periods: Sequence[int] = (20, 50),
    sma_periods: Sequence[int] = (200,),
    include_volume: bool = True,
    include_rsi: bool = True,
    rsi_length: int = 14,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
    max_positions: Optional[int] = None,
) -> None:
    if positions is None or positions.empty:
        logger.info("Position chart generation skipped (no positions).")
        return
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        logger.warning("Position chart generation skipped (matplotlib not available): %s", exc)
        return

    os.makedirs(output_dir, exist_ok=True)
    positions = positions.copy()
    positions["entry_date"] = pd.to_datetime(positions["entry_date"], errors="coerce")
    positions["exit_date"] = pd.to_datetime(positions["exit_date"], errors="coerce")

    trades = trades.copy()
    trades["date"] = pd.to_datetime(trades["date"], errors="coerce")

    count = 0
    for _, pos in positions.iterrows():
        if max_positions is not None and count >= max_positions:
            break
        ticker = pos.get("symbol")
        if not ticker:
            continue
        df = data_by_ticker.get(ticker)
        if df is None or df.empty:
            continue
        entry_date = pos.get("entry_date")
        exit_date = pos.get("exit_date")

        pos_trades = pd.DataFrame()
        pos_entries = pd.DataFrame()
        pos_exits = pd.DataFrame()
        pos_adds = pd.DataFrame()
        if "position_id" in trades.columns:
            pos_trades = trades[trades["position_id"] == pos.get("position_id")]
            if not pos_trades.empty:
                pos_entries = pos_trades[pos_trades["action"].isin(_ENTRY_ACTIONS)].sort_values("date")
                pos_exits = pos_trades[pos_trades["action"].isin(_EXIT_ACTIONS)].sort_values("date")
                if include_adds:
                    pos_adds = pos_trades[pos_trades["action"].isin(_ADD_ACTIONS)]

        if pd.isna(entry_date) and not pos_entries.empty:
            entry_date = pos_entries["date"].iloc[0]
        if pd.isna(exit_date) and not pos_exits.empty:
            exit_date = pos_exits["date"].iloc[-1]
        if pd.isna(entry_date) or pd.isna(exit_date):
            continue

        entry_idx = _index_position(df.index, pd.Timestamp(entry_date))
        exit_idx = _index_position(df.index, pd.Timestamp(exit_date))
        if entry_idx is None or exit_idx is None:
            continue

        start_idx = max(entry_idx - window_bars_before, 0)
        end_idx = min(exit_idx + window_bars_after + 1, len(df))
        plot_df = df.iloc[start_idx:end_idx]
        if plot_df.empty:
            continue

        axes = []
        if include_volume and include_rsi:
            fig, axes = plt.subplots(
                3,
                1,
                figsize=figsize,
                dpi=dpi,
                gridspec_kw={"height_ratios": [3, 1, 1]},
                sharex=True,
            )
        elif include_volume or include_rsi:
            fig, axes = plt.subplots(
                2,
                1,
                figsize=figsize,
                dpi=dpi,
                gridspec_kw={"height_ratios": [3, 1]},
                sharex=True,
            )
        else:
            fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
            axes = [ax]
        ax = axes[0]
        ax_vol = None
        ax_rsi = None
        if include_volume and include_rsi:
            ax_vol = axes[1]
            ax_rsi = axes[2]
        elif include_volume:
            ax_vol = axes[1]
        elif include_rsi:
            ax_rsi = axes[1]
        x = np.arange(len(plot_df))
        if style == "candles":
            _plot_candles(ax, plot_df, x)
        else:
            ax.plot(x, plot_df["Close"], color="#1f77b4", linewidth=1.2, label="Close")
        _plot_indicators(ax, plot_df, x, ema_periods=ema_periods, sma_periods=sma_periods)
        if ax_vol is not None:
            _plot_volume(ax_vol, plot_df, x)
        if ax_rsi is not None:
            _plot_rsi(ax_rsi, plot_df, x, rsi_length, rsi_overbought, rsi_oversold)

        _plot_markers(ax, plot_df, pos_entries)
        if include_partials and "partial" in pos_exits.columns and pos_exits["partial"].astype(bool).any():
            partials = pos_exits[pos_exits["partial"].astype(bool)]
            fulls = pos_exits[~pos_exits["partial"].astype(bool)]
            _plot_markers(ax, plot_df, fulls)
            _plot_markers(ax, plot_df, partials)
        else:
            _plot_markers(ax, plot_df, pos_exits)
        if include_adds and not pos_adds.empty:
            _plot_markers(ax, plot_df, pos_adds)

        price_span = float(plot_df["High"].max() - plot_df["Low"].min())
        y_offset = max(price_span * 0.06, 0.01)
        if not pos_entries.empty:
            entry = pos_entries.iloc[0]
            _annotate_point(ax, plot_df, entry["date"], entry["price"], "ENTRY", "#00A651", y_offset)
        if not pos_exits.empty:
            exit_row = pos_exits.iloc[-1]
            _annotate_point(ax, plot_df, exit_row["date"], exit_row["price"], "EXIT", "#E23D28", -y_offset)

        title = f"{ticker} - {pos.get('strategy')} - {pos.get('position_id')}"
        _finalize_axes(ax, plot_df, title)
        ax.legend(loc="best", fontsize=8)
        if ax_vol is not None:
            ax_vol.grid(True, alpha=0.2)
        if ax_rsi is not None:
            ax_rsi.grid(True, alpha=0.2)

        safe_name = _safe_filename(f"{ticker}_{pos.get('position_id')}")
        output_path = os.path.join(output_dir, f"{safe_name}.{file_format}")
        fig.savefig(output_path, bbox_inches="tight")
        plt.close(fig)
        count += 1


def build_charts_manifest(
    positions: pd.DataFrame,
    output_dir: str,
    *,
    file_format: str = "png",
    manifest_path: Optional[str] = None,
) -> pd.DataFrame:
    if positions is None or positions.empty:
        return pd.DataFrame()
    rows = []
    for _, pos in positions.iterrows():
        symbol = pos.get("symbol")
        position_id = pos.get("position_id")
        if not symbol or pd.isna(position_id):
            continue
        safe_name = _safe_filename(f"{symbol}_{position_id}")
        filename = f"{safe_name}.{file_format}"
        file_path = os.path.join(output_dir, filename)
        if not os.path.exists(file_path):
            continue
        realized_pnl = pos.get("realized_pnl")
        return_pct = pos.get("return_pct")
        outcome = "flat"
        try:
            if realized_pnl is not None and float(realized_pnl) > 0:
                outcome = "win"
            elif realized_pnl is not None and float(realized_pnl) < 0:
                outcome = "loss"
        except Exception:
            outcome = "flat"
        rows.append({
            "filename": filename,
            "size_bytes": os.path.getsize(file_path),
            "chart_type": "position",
            "symbol": symbol,
            "strategy": pos.get("strategy"),
            "book": pos.get("book"),
            "position_id": position_id,
            "entry_id": pos.get("entry_id"),
            "entry_date": pos.get("entry_date"),
            "exit_date": pos.get("exit_date"),
            "holding_days": pos.get("holding_days"),
            "exit_reason": pos.get("exit_reason"),
            "stop_type_at_exit": pos.get("stop_type_at_exit"),
            "realized_pnl": realized_pnl,
            "return_pct": return_pct,
            "outcome": outcome,
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if manifest_path:
        df.to_csv(manifest_path, index=False)
    return df
