# -*- coding: utf-8 -*-
"""
MT5 数据源
"""
import os
import czsc
import pandas as pd
from datetime import datetime, timedelta
from czsc import Freq, RawBar
from typing import List
from tqdm import tqdm
from loguru import logger
from vnpy.trader.database import get_database
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData, TickData


# 获取数据库实例
database = get_database()


def format_kline(kline: pd.DataFrame, freq: Freq) -> List[RawBar]:
    """MT5 K线数据转换

    :param kline: MT5 mongodb 数据
    :param freq: K线周期
    :return: 转换好的K线数据
    """
    bars = []
    dt_key = "trade_time" if "分钟" in freq.value else "trade_date"
    kline = kline.sort_values(dt_key, ascending=True, ignore_index=True)
    records = kline.to_dict("records")

    for i, record in enumerate(records):
        # 将每一根K线转换成 RawBar 对象
        bar = RawBar(
            symbol=record["ts_code"],
            dt=pd.to_datetime(record[dt_key]),
            id=i,
            freq=freq,
            open=record["open"],
            close=record["close"],
            high=record["high"],
            low=record["low"],
            vol=record["vol"],
            amount=0,
        )
        bars.append(bar)
        
    return bars


def get_raw_bars(symbol, freq, sdt, edt, fq="后复权", raw_bar=True):
    freq = str(freq)
    adj = "qfq" if fq == "前复权" else "hfq"
    bars = []
    vnpy_data : List[BarData] =   database.load_bar_data(symbol,exchange=Exchange.OTC,interval=Interval.MINUTE,start=datetime.strptime(sdt,"%Y%m%d"), end=datetime.strptime(edt,"%Y%m%d"))
    for i, row in enumerate(tqdm(vnpy_data, desc="加载外汇mongodb数据库数据:")):  
        bar = RawBar(
            symbol=row.symbol,
            id=i,
            freq=Freq.F1,
            dt=row.datetime,
            open=row.open_price,
            close=row.close_price,
            high=row.high_price,
            low=row.low_price,
            vol=row.volume,
            amount=row.volume * row.close_price,
        )
        bars.append(bar)
    return bars


if __name__ == "__main__":
   bars = get_raw_bars(symbol="EURUSD",freq="1分钟",sdt="20230101",edt="20230501")
   print(bars)