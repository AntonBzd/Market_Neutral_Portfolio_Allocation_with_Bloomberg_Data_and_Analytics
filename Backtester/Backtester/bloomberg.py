# bloomberg.py
import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Iterable, Optional, Tuple
import os

import blpapi
import pandas as pd
import numpy as np


def _month_dates(start: dt.date, end: dt.date, day: int = 28) -> List[pd.Timestamp]:
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    cur = pd.Timestamp(year=start.year, month=start.month, day=1)

    out = []
    while cur <= end:
        last_day = (cur + pd.offsets.MonthEnd(0)).day
        d = min(day, last_day)
        anchor = pd.Timestamp(year=cur.year, month=cur.month, day=d)
        if start <= anchor <= end:
            out.append(anchor)
        cur = cur + pd.offsets.MonthBegin(1)
    return out


def _chunks(seq: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _normalize_member_ticker(raw: str) -> str:

    s = (raw or "").strip()
    if not s:
        return s
    lower = s.lower()
    if any(x in lower for x in [" equity", " index", " curncy", " gov", " corp"]):
        return s
    return f"{s} Equity"


@dataclass
class BloombergConfig:
    host: str = "localhost"
    port: int = 8194
    refdata_service: str = "//blp/refdata"


class BLP:
    """ BDH + BDS pour INDX_MEMBERS """

    def __init__(self, config: Optional[BloombergConfig] = None):
        self.config = config or BloombergConfig()
        opts = blpapi.SessionOptions()
        opts.setServerHost(self.config.host)
        opts.setServerPort(self.config.port)

        self.session = blpapi.Session(opts)
        if not self.session.start():
            raise RuntimeError("Failed Bbg session.")
        if not self.session.openService(self.config.refdata_service):
            raise RuntimeError(f"Failed to open {self.config.refdata_service}")
        self.refDataSvc = self.session.getService(self.config.refdata_service)

    def close(self) -> None:
        try:
            self.session.stop()
        except Exception:
            pass

    def bdh(
        self,
        strSecurity,
        strFields,
        startdate: dt.datetime,
        enddate: dt.datetime,
        per: str = "DAILY",
        perAdj: str = "CALENDAR",
        days: str = "NON_TRADING_WEEKDAYS",
        fill: str = "PREVIOUS_VALUE",
        curr: Optional[str] = None,
    ) -> pd.DataFrame:
        request = self.refDataSvc.createRequest("HistoricalDataRequest")

        if isinstance(strFields, str):
            strFields = [strFields]
        if isinstance(strSecurity, str):
            strSecurity = [strSecurity]

        for security in strSecurity:
            request.append("securities", security)
        for field in strFields:
            request.append("fields", field)

        request.set("startDate", startdate.strftime("%Y%m%d"))
        request.set("endDate", enddate.strftime("%Y%m%d"))
        request.set("periodicitySelection", per)
        request.set("periodicityAdjustment", perAdj)
        request.set("nonTradingDayFillOption", days)
        request.set("nonTradingDayFillMethod", fill)
        if curr:
            request.set("currency", curr)

        self.session.sendRequest(request)

        data = []
        keys = []

        while True:
            event = self.session.nextEvent()
            if event.eventType() not in [blpapi.Event.RESPONSE, blpapi.Event.PARTIAL_RESPONSE]:
                continue

            for msg in event:
                securityDataArray = msg.getElement("securityData")
                fieldData = securityDataArray.getElement("fieldData")
                fieldDataList = [fieldData.getValueAsElement(i) for i in range(fieldData.numValues())]

                df = pd.DataFrame()
                for fld in fieldDataList:
                    dt_ = fld.getElementAsDatetime("date")
                    for i in range(fld.numElements()):
                        el = fld.getElement(i)
                        if el.name() == "date":
                            continue
                        df.loc[pd.Timestamp(dt_), str(el.name())] = el.getValue()

                df.index = pd.to_datetime(df.index)
                df.replace("#N/A History", np.nan, inplace=True)

                keys.append(securityDataArray.getElementAsString("security"))
                data.append(df)

            if event.eventType() == blpapi.Event.RESPONSE:
                break

        if len(data) == 0:
            return pd.DataFrame()

        if len(strSecurity) == 1:
            out = pd.concat(data, axis=1)
            out.columns.name = "Field"
        else:
            out = pd.concat(data, keys=keys, axis=1, names=["Security", "Field"])
            out = out.swaplevel(axis=1)  # Field, Security
            out = out.sort_index(axis=1, level=0)

        out.index.name = "Date"
        return out

    def bds_index_members(self, index_ticker: str, asof_yyyymmdd: str) -> List[str]:
        """
        INDX_MEMBERS avec format d'override END_DATE_OVERRIDE=yyyymmdd 
        """
        req = self.refDataSvc.createRequest("ReferenceDataRequest")
        req.append("securities", index_ticker)
        req.append("fields", "INDX_MEMBERS")

        ovs = req.getElement("overrides")
        ov = ovs.appendElement()
        ov.setElement("fieldId", "END_DATE_OVERRIDE")
        ov.setElement("value", asof_yyyymmdd)

        self.session.sendRequest(req)

        members: List[str] = []
        while True:
            ev = self.session.nextEvent()
            if ev.eventType() not in (blpapi.Event.PARTIAL_RESPONSE, blpapi.Event.RESPONSE):
                continue

            for msg in ev:
                secDataArr = msg.getElement("securityData")
                for si in range(secDataArr.numValues()):
                    secData = secDataArr.getValueAsElement(si)
                    fieldData = secData.getElement("fieldData")

                    if not fieldData.hasElement("INDX_MEMBERS"):
                        continue

                    bulk = fieldData.getElement("INDX_MEMBERS")
                    for i in range(bulk.numValues()):
                        row = bulk.getValueAsElement(i)

                        candidate_names = [
                            "Member Ticker and Exchange Code",
                            "Member Ticker & Exchange Code",
                            "Member",
                            "Ticker",
                        ]

                        val = None
                        for name in candidate_names:
                            if row.hasElement(name):
                                try:
                                    val = row.getElementAsString(name)
                                    break
                                except Exception:
                                    pass

                        if val is None:
                            for k in range(row.numElements()):
                                el = row.getElement(k)
                                try:
                                    val = el.getValueAsString()
                                    if val:
                                        break
                                except Exception:
                                    continue

                        if val:
                            members.append(_normalize_member_ticker(val))

            if ev.eventType() == blpapi.Event.RESPONSE:
                break

        # dédoublonne en gardant l'ordre
        seen = set()
        out = []
        for m in members:
            if m and m not in seen:
                out.append(m)
                seen.add(m)
        return out


def fetch_bbg_data(
    start: dt.date,
    end: dt.date,
    index_ticker: str = "SPX Index",
    benchmark_ticker: str = "SPX Index",
    anchor_day: int = 28, 
    cache_dir: Optional[str] = "./cache",
    batch_size: int = 150,
) -> Tuple[pd.DataFrame, Dict[pd.Timestamp, List[str]]]:
 
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
 
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
 
    members_cache = os.path.join(
        cache_dir,
        f"members_ALIGNED_{index_ticker.replace(' ', '_')}_{start_ts:%Y%m%d}_{end_ts:%Y%m%d}.pkl"
    ) if cache_dir else None
 
    prices_cache = os.path.join(
        cache_dir,
        f"pxlast_ALIGNED_{index_ticker.replace(' ', '_')}_{start_ts:%Y%m%d}_{end_ts:%Y%m%d}.parquet"
    ) if cache_dir else None
 
    if prices_cache and os.path.exists(prices_cache) and members_cache and os.path.exists(members_cache):
        prices = pd.read_parquet(prices_cache)
        prices.index = pd.to_datetime(prices.index)
        members_by_month = pd.read_pickle(members_cache)
        return prices, members_by_month
 
    blp = BLP()
    try:
        # Fetch benchmark d'abord, donne LES dates exactes Bloomberg
        bench = blp.bdh(
            strSecurity=benchmark_ticker,
            strFields="PX_LAST",
            startdate=start_ts.to_pydatetime(),
            enddate=end_ts.to_pydatetime(),
            per="MONTHLY",
            perAdj="CALENDAR",
            fill="PREVIOUS_VALUE",
        )
        if bench.empty:
            raise RuntimeError("Benchmark fetch returned empty dataframe.")
 
        bench_px = bench[["PX_LAST"]].rename(columns={"PX_LAST": benchmark_ticker})
        bench_px.index = pd.to_datetime(bench_px.index)
        month_dates = bench_px.index.tolist()
 
        # Membres de l'index sur EXACTEMENT ces dates
        members_by_month: Dict[pd.Timestamp, List[str]] = {}
        for t in month_dates:
            members_by_month[t] = blp.bds_index_members(index_ticker, t.strftime("%Y%m%d"))
 
        # Union des tickers
        universe = sorted({x for lst in members_by_month.values() for x in lst if x})
 
        # Fetch prices actions en batches
        all_px = []
        for chunk in _chunks(universe, batch_size):
            df = blp.bdh(
                strSecurity=chunk,
                strFields="PX_LAST",
                startdate=start_ts.to_pydatetime(),
                enddate=end_ts.to_pydatetime(),
                per="MONTHLY",
                perAdj="CALENDAR",
                fill="PREVIOUS_VALUE",
            )
            if df.empty:
                continue
            px = df["PX_LAST"].copy()
            px.index = pd.to_datetime(px.index)
            all_px.append(px)
 
        prices_assets = pd.concat(all_px, axis=1) if all_px else pd.DataFrame(index=bench_px.index)
 
    finally:
        blp.close()
 
    # index = celui du benchmark
    prices = prices_assets.reindex(bench_px.index).join(bench_px, how="left")
    prices.index = pd.to_datetime(prices.index)
 
    if cache_dir:
        prices.to_parquet(prices_cache)
        pd.to_pickle(members_by_month, members_cache)
 
    return prices, members_by_month



def export_portfolio_weights_to_import_in_bbg(
    weights_by_date: dict[pd.Timestamp, pd.Series],
    portfolio_name: str,
    filepath: str,
    sheet_name: str = "HOLDINGS",
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> None:
    """
      Format colonnes : DATE / PORTFOLIO / SECURITY / WEIGHT
    """
    rows = []
    for d, w in weights_by_date.items():
        d = pd.Timestamp(d)
        if start is not None and d < pd.Timestamp(start):
            continue
        if end is not None and d > pd.Timestamp(end):
            continue
 
        w = w.dropna()
        w = w[w != 0]
        if w.empty:
            continue
 
        df = pd.DataFrame(
            {
                "DATE": [d.strftime("%Y-%m-%d")] * len(w),
                "PORTFOLIO": [portfolio_name] * len(w),
                "SECURITY": w.index.astype(str),
                "WEIGHT": w.values.astype(float),
            }
        )
        rows.append(df)
 
    out = pd.concat(rows, axis=0, ignore_index=True) if rows else pd.DataFrame(
        columns=["DATE", "PORTFOLIO", "SECURITY", "WEIGHT"]
    )
 
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        out.to_excel(writer, index=False, sheet_name=sheet_name)