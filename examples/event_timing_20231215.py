      
      
import os
os.environ['base_path'] = r"D:\CTA研究"    # 回测结果保存路径
os.environ['signals_module_name'] = 'czsc.signals'             
from typing import List
from copy import deepcopy
from pathlib import Path
from multiprocessing import cpu_count
from concurrent.futures import ProcessPoolExecutor, as_completed
import glob
import json
import hashlib
import pandas as pd
import streamlit as st
import plotly.express as px
from loguru import logger
from stqdm import stqdm as tqdm
import czsc
from czsc import CzscStrategyBase, CzscTrader, KlineChart, Freq, Operate, Position
from czsc.utils.bar_generator import freq_end_time
from czsc.connectors.research import get_symbols, get_raw_bars
from streamlit_option_menu import option_menu
from streamlit_extras.mandatory_date_range import date_range_picker


st.set_page_config(layout="wide", page_title="CZSC策略回放", page_icon="📈")


class JsonStreamStrategy(CzscStrategyBase):
    """读取 streamlit 传入的 json 策略，进行回测"""

    @property
    def positions(self) -> List[Position]:
        """返回当前的持仓策略"""
        json_strategies = self.kwargs.get("json_strategies")
        assert json_strategies, "请在初始化策略时，传入参数 json_strategies"
        positions = []
        for _, pos in json_strategies.items():
            pos["symbol"] = self.symbol
            positions.append(Position.load(pos))
        return positions


def show_trader(trader: CzscTrader, files):
    if not trader.freqs or not trader.kas or not trader.positions:
        st.error("当前 trader 没有回测数据")
        return

    freqs = trader.freqs
    tabs = st.tabs(freqs + ['回测记录', '策略详情'])

    i = 0
    for freq in freqs:
        c = trader.kas[freq]
        df = pd.DataFrame(c.bars_raw)
        kline = KlineChart(n_rows=3, row_heights=(0.5, 0.3, 0.2), title='', width="100%", height=600)
        kline.add_kline(df, name="")

        if len(c.bi_list) > 0:
            bi = pd.DataFrame(
                [{'dt': x.fx_a.dt, "bi": x.fx_a.fx} for x in c.bi_list]
                + [{'dt': c.bi_list[-1].fx_b.dt, "bi": c.bi_list[-1].fx_b.fx}]
            )
            fx = pd.DataFrame([{'dt': x.dt, "fx": x.fx} for x in c.fx_list])
            kline.add_scatter_indicator(fx['dt'], fx['fx'], name="分型", row=1, line_width=1.2, visible=True)
            kline.add_scatter_indicator(bi['dt'], bi['bi'], name="笔", row=1, line_width=1.5)

        kline.add_sma(df, ma_seq=(5, 20, 120, 240), row=1, visible=False, line_width=1)
        kline.add_vol(df, row=2, line_width=1)
        kline.add_macd(df, row=3, line_width=1)

        for pos in trader.positions:
            bs_df = pd.DataFrame([x for x in pos.operates if x['dt'] >= c.bars_raw[0].dt])
            if not bs_df.empty:
                bs_df['dt'] = bs_df['dt'].apply(lambda x: freq_end_time(x, Freq(freq)))
                bs_df['tag'] = bs_df['op'].apply(lambda x: 'triangle-up' if x == Operate.LO else 'triangle-down')
                bs_df['color'] = bs_df['op'].apply(lambda x: 'red' if x == Operate.LO else 'silver')
                kline.add_scatter_indicator(
                    bs_df['dt'],
                    bs_df['price'],
                    name=pos.name,
                    text=bs_df['op_desc'],
                    row=1,
                    mode='text+markers',
                    marker_size=15,
                    marker_symbol=bs_df['tag'],
                    marker_color=bs_df['color'],
                )

        with tabs[i]:
            config = {
                "scrollZoom": True,
                "displayModeBar": True,
                "displaylogo": False,
                'modeBarButtonsToRemove': [
                    'toggleSpikelines',
                    'select2d',
                    'zoomIn2d',
                    'zoomOut2d',
                    'lasso2d',
                    'autoScale2d',
                    'hoverClosestCartesian',
                    'hoverCompareCartesian',
                ],
            }
            st.plotly_chart(kline.fig, use_container_width=True, config=config)
        i += 1

    with tabs[i]:
        with st.expander("查看所有开平交易记录", expanded=False):
            show_cols = ['策略标记', '交易方向', '盈亏比例', '开仓时间', '平仓时间', '持仓K线数', '事件序列']
            st.dataframe(st.session_state.pos_pairs[show_cols], use_container_width=True, hide_index=True)

        df = pd.DataFrame([x.evaluate() for x in trader.positions])
        st.dataframe(df, use_container_width=True)

        with st.expander("分别查看多头和空头的表现", expanded=False):
            df1 = pd.DataFrame([x.evaluate('多头') for x in trader.positions])
            st.dataframe(df1, use_container_width=True)

            df2 = pd.DataFrame([x.evaluate('空头') for x in trader.positions])
            st.dataframe(df2, use_container_width=True)

    i += 1
    with tabs[i]:
        with st.expander("查看最新信号", expanded=False):
            if len(trader.s):
                s = {k: v for k, v in trader.s.items() if len(k.split('_')) == 3}
                st.write(s)
            else:
                st.warning("当前没有信号配置信息")

        for file in files:
            with st.expander(f"持仓策略配置：{file.name}", expanded=False):
                st.json(json.loads(file.getvalue().decode("utf-8")), expanded=True)


def init_trader(files, symbol, bar_sdt, sdt, edt):
    """初始化回放参数

    :param files: 策略文件
    :param symbol: 交易标的
    :param bar_sdt: 行情开始日期
    :param sdt: 回放开始日期
    :param edt: 回放结束日期
    """
    assert pd.to_datetime(bar_sdt) < pd.to_datetime(sdt) < pd.to_datetime(edt), "回放起止日期设置错误"

    json_strategies = {file.name: json.loads(file.getvalue().decode("utf-8")) for file in files}
    tactic: CzscStrategyBase = JsonStreamStrategy(
        symbol=symbol, signals_module_name=os.environ['signals_module_name'], json_strategies=json_strategies
    )
    bars = get_raw_bars(symbol, tactic.base_freq, sdt=bar_sdt, edt=edt)
    bg, bars_right = tactic.init_bar_generator(bars, sdt=sdt)
    trader = CzscTrader(bg=bg, positions=deepcopy(tactic.positions), signals_config=deepcopy(tactic.signals_config))

    st.session_state.trader = deepcopy(trader)
    st.session_state.bars_right = deepcopy(bars_right)
    st.session_state.bars_index = 0
    st.session_state.run = False

    # 跑一遍回测，生成持仓记录，用于回放时给人工检查策略一个参考
    for bar in bars_right:
        trader.on_bar(bar)

    assert trader.positions, "当前策略没有持仓记录"
    pairs = [pd.DataFrame(pos.pairs) for pos in trader.positions if pos.pairs]
    st.session_state.pos_pairs = pd.concat(pairs, ignore_index=True)


def replay(files):
    """CTA策略回放"""
    with st.sidebar:
            with st.form(key='my_form_replay'):
                col1, col2 = st.columns([1, 1])
                symbol = col1.selectbox("选择交易标的：", get_symbols('ALL'), index=0)
                bar_sdt = col2.date_input(label='行情开始日期：', value=pd.to_datetime('2018-01-01'))
                sdt, edt = date_range_picker("回放起止日期", default_start=pd.to_datetime('2019-01-01'), default_end=pd.to_datetime('2022-01-01'))
                submitted = st.form_submit_button(label='设置回放参数')

    if submitted:
        init_trader(files, symbol, bar_sdt, sdt, edt)

    if files and hasattr(st.session_state, 'trader'):
        trader = deepcopy(st.session_state.trader)
        bars_right = deepcopy(st.session_state.bars_right)
        bars_num = len(bars_right)

        c1, c2, c3, c4, c5 = st.columns([5, 5, 5, 5, 25])

        bar_edt = bars_right[st.session_state.bars_index].dt
        target_bar_edt = c5.text_input('行情定位到指定时间：', placeholder=bar_edt.strftime('%Y-%m-%d %H:%M'), key="bar_edt")
        if target_bar_edt:
            target_bar_edt = pd.to_datetime(target_bar_edt)
            for i, bar in enumerate(bars_right):
                if bar.dt >= target_bar_edt:
                    st.session_state.bars_index = i
                    break

        if c1.button('行情播放'):
            st.session_state.run = True
        if c2.button('行情暂停'):
            st.session_state.run = False
        if c3.button('左移一根K线'):
            st.session_state.bars_index -= 1
        if c4.button('右移一根K线'):
            st.session_state.bars_index += 1

        # 约束 bars_index 的范围在 [0, bars_num]
        st.session_state.bars_index = max(0, st.session_state.bars_index)
        st.session_state.bars_index = min(st.session_state.bars_index, bars_num)

        suffix = f"共{bars_num}根K线" if bars_num < 1000 else f"共{bars_num}根K线，回放数据量较大（超过1000根K线），建议缩小回放时间范围"
        st.caption(f"行情播放时间范围：{bars_right[0].dt} - {bars_right[-1].dt}; 当前K线：{bar_edt}；{suffix}")

        if st.session_state.run:
            idx = st.session_state.bars_index
            bars1 = bars_right[0: idx].copy()
            while bars1:
                bar_ = bars1.pop(0)
                trader.on_bar(bar_)

            bars2 = bars_right[idx:].copy()
            with st.empty():
                while bars2:
                    bar_ = bars2.pop(0)
                    trader.on_bar(bar_)
                    show_trader(trader, files)
                    st.session_state.bars_index += 1

        else:
            bars2 = bars_right[: st.session_state.bars_index + 1].copy()
            with st.empty():
                while bars2:
                    bar_ = bars2.pop(0)
                    trader.on_bar(bar_)
                show_trader(trader, files)
    else:
        st.warning("请上传策略文件, 文件格式为 json，配置回放参数")
    
# ======================================================================================================================
# 以下是回测的代码
# ======================================================================================================================

@st.cache_data()
def read_holds_and_pairs(files_traders, pos_name, fee=1):
    holds, pairs = [], []
    for file in tqdm(files_traders):
        try:
            trader = czsc.dill_load(file)
            pos = trader.get_position(pos_name)
            if not pos.holds:
                logger.info(f"{trader.symbol} {pos_name} 无持仓，跳过")
                continue

            hd = pd.DataFrame(pos.holds)
            hd['symbol'] = trader.symbol
            hd = czsc.subtract_fee(hd, fee=fee)
            holds.append(hd)

            pr = pd.DataFrame(pos.pairs)
            pairs.append(pr)
        except Exception as e:
            logger.warning(f"{file} {pos_name} 读取失败: {e}")

    dfh = pd.concat(holds, ignore_index=True)
    dfp = pd.concat(pairs, ignore_index=True)
    return dfh, dfp


@st.cache_data()
def get_daily_nv(df):
    """获取每日净值"""
    res = []
    for symbol, hd in tqdm(df.groupby('symbol')):
        hd = hd.sort_values('dt', ascending=True)
        try:
            daily = hd.groupby('date').agg({'edge_pre_fee': 'sum', 'edge_post_fee': 'sum'}).reset_index()
            daily['symbol'] = symbol
            res.append(daily)
        except Exception as e:
            logger.exception(f"{symbol} 日收益获取失败: {e}")

    dfr = pd.concat(res, ignore_index=True)
    return dfr


def show_pos_detail(file_trader, pos_name):
    """显示持仓策略详情"""
    trader = czsc.dill_load(file_trader)
    pos = trader.get_position(pos_name)
    with st.expander(f"{pos_name} 持仓策略详情", expanded=False):
        _pos = pos.dump()
        _pos.pop('symbol')
        st.json(_pos)


def show_backtest_results(file_traders, pos_name, fee=1):
    dfh, dfp = read_holds_and_pairs(file_traders, pos_name, fee=fee)
    dfr = get_daily_nv(dfh)
    show_pos_detail(file_traders[0], pos_name)

    st.subheader("一、单笔收益评价")

    pp = czsc.PairsPerformance(dfp)
    # st.write(pp.basic_info)
    df1 = pp.agg_statistics('标的代码')
    _res = pp.basic_info
    _res['标的代码'] = "全部品种"
    df1 = pd.concat([pd.DataFrame([_res]), df1], ignore_index=True)
    _cols = [
        '标的代码',
        '开始时间',
        '结束时间',
        '交易标的数量',
        '总体交易次数',
        '平均持仓K线数',
        '平均单笔收益',
        '单笔收益标准差',
        '交易胜率',
        '单笔盈亏比',
        '累计盈亏比',
        '盈亏平衡点',
        '每根K线收益',
    ]
    df1 = df1[_cols].set_index('标的代码')
    color_cols = ['交易标的数量', '总体交易次数', '平均持仓K线数', '平均单笔收益', '单笔收益标准差',
                  '交易胜率', '单笔盈亏比', '累计盈亏比', '盈亏平衡点', '每根K线收益']
    df1 = df1.style.format('{0:,.2f}', subset=color_cols, na_rep="-").background_gradient(cmap='RdYlGn_r', subset=color_cols)

    st.dataframe(df1, use_container_width=True)

    st.divider()

    st.subheader("二、品种等权收益曲线")
    dfg = dfr.groupby('date').agg({'edge_pre_fee': 'mean', 'edge_post_fee': 'mean'}).cumsum()
    dfg.rename({'edge_pre_fee': '等权费前收益', 'edge_post_fee': f'双边扣费{2*fee}BP'}, axis=1, inplace=True)

    fig = px.line(dfg, x=dfg.index, y=['等权费前收益', f'双边扣费{2*fee}BP'], labels=[], title="全部品种日收益等权")
    st.plotly_chart(fig, use_container_width=True, height=600)

    dfg['dt'] = dfg.index.to_list()
    stats = []
    for col in ['等权费前收益', f'双边扣费{2*fee}BP']:
        dfg_ = dfg[['dt', col]].copy().rename(columns={col: 'edge'}).reset_index(drop=True)
        dfg_['edge'] = dfg_['edge'].diff()
        stats_ = czsc.net_value_stats(dfg_, sub_cost=False)
        stats_['name'] = col
        stats.append(stats_)
    st.dataframe(pd.DataFrame(stats).set_index('name'), use_container_width=True)


def symbol_backtest(strategies, symbol, bar_sdt, sdt, edt, results_path):
    """回测单个标的

    :param strategies: 策略配置
    :param symbol: 标的代码
    :param bar_sdt: 行情开始日期
    :param sdt: 回测开始日期
    :param edt: 回测结束日期
    :param results_path: 回测结果保存路径
    """
    file_trader = results_path / f"{symbol}.trader"
    if file_trader.exists():
        logger.info(f"{symbol} 已回测，跳过")
        return

    try:
        tactic = JsonStreamStrategy(json_strategies=strategies, symbol=symbol)
        bars = get_raw_bars(symbol, tactic.base_freq, sdt=bar_sdt, edt=edt)
        trader = tactic.backtest(bars, sdt=sdt)
        czsc.dill_dump(trader, file_trader)
    except:
        logger.exception(f"{symbol} 回测失败")


@st.cache_data(ttl=60 * 60 * 24)
def backtest_all(strategies, results_path):
    """回测全部标的

    :param strategies: 策略配置
    :param results_path: 回测结果保存路径
    """
    bar_sdt = st.session_state.bar_sdt
    gruop = st.session_state.gruop
    sdt = st.session_state.sdt
    edt = st.session_state.edt
    max_workers = st.session_state.max_workers
    symbols = get_symbols(gruop)

    if max_workers <= 1:
        for symbol in tqdm(symbols, desc="On Bar 回测进度"):
            symbol_backtest(strategies, symbol, bar_sdt, sdt, edt, results_path)
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            tasks = [executor.submit(symbol_backtest, strategies, symbol, bar_sdt, sdt, edt, results_path)
                    for symbol in symbols]
            for future in tqdm(as_completed(tasks), desc="On Bar 回测进度", total=len(tasks)):
                future.result()


def backtest(files):
    with st.sidebar:
        with st.form(key='my_form_czsc1'):
            col1, col2 = st.columns([1, 1])
            bar_sdt = col2.date_input(label='行情开始日期', value=pd.to_datetime('2018-01-01'))
            gruop = col1.selectbox(label="回测品类", options=['A股主要指数', 'A股场内基金', '中证500成分股', '期货主力'], index=3)
            sdt, edt = date_range_picker("回测起止日期", default_start=pd.to_datetime('2019-01-01'), default_end=pd.to_datetime('2022-01-01'))
            col1, col2 = st.columns([1, 1])
            max_workers = int(col1.number_input(label='指定进程数量', value=cpu_count() // 4, min_value=1, max_value=cpu_count() // 2))
            fee = int(col2.number_input(label='单边手续费（单位：BP）', value=2, min_value=0, max_value=100))
            submit_button = st.form_submit_button(label='开始回测')

    if submit_button:
        st.session_state.files = files
        st.session_state.bar_sdt = bar_sdt
        st.session_state.gruop = gruop
        st.session_state.sdt = sdt
        st.session_state.edt = edt
        st.session_state.max_workers = max_workers
        st.session_state.fee = fee

    if not hasattr(st.session_state, 'files'):
        st.warning("请先设置策略回测参数")
        st.stop()

    files = st.session_state.files
    bar_sdt = st.session_state.bar_sdt
    gruop = st.session_state.gruop
    sdt = st.session_state.sdt
    edt = st.session_state.edt
    max_workers = st.session_state.max_workers
    fee = st.session_state.fee

    strategies = {file.name: json.loads(file.getvalue().decode("utf-8")) for file in files}
    hash_code = hashlib.sha256(f"{str(strategies)}".encode('utf-8')).hexdigest()[:8].upper()
    results_path = Path(os.getenv("base_path")) / "CTA策略回测" / f"{sdt}_{edt}_{hash_code}" / gruop
    results_path.mkdir(exist_ok=True, parents=True)

    with st.sidebar.expander("策略详情", expanded=False):
        tactic = JsonStreamStrategy(json_strategies=strategies, symbol='symbol')
        st.caption(f"K线周期列表：{tactic.freqs}")
        st.caption("独立信号列表：")
        st.json(tactic.unique_signals)
        st.caption("信号函数配置：")
        st.json(tactic.signals_config)

    backtest_all(strategies, results_path)

    file_traders = glob.glob(fr"{results_path}\*.trader")
    if not file_traders:
        st.warning("当前回测参数下，没有任何标的回测结果；请调整回测参数后重试")
        st.stop()

    all_pos_names = [x.name for x in czsc.dill_load(file_traders[0]).positions]
    tabs = st.tabs(['全部品种', '选择特定品种组合'])

    with tabs[0]:
        pos_name = st.selectbox("选择持仓", all_pos_names, index=0, key="pos_name")
        show_backtest_results(file_traders, pos_name, fee=fee)

    with tabs[1]:
        candidates = [Path(x).stem for x in file_traders]
        sel_symbols = []
        with st.form(key='my_form_czsc_2'):
            col1, col2 = st.columns([1, 3])
            pos_name_a = col1.selectbox("选择持仓", all_pos_names, index=0, key="pos_name_a")
            sel_symbols = col2.multiselect("选择品种", candidates, default=candidates[:3])
            submit_button = st.form_submit_button(label='分析特定品种组合')

        if not sel_symbols:
            st.warning("请先选择品种组合")
            st.stop()

        sel_files= [x for x in file_traders if Path(x).stem in sel_symbols]
        show_backtest_results(sel_files, pos_name_a, fee=fee)


# ======================================================================================================================
# 以下是主函数
# ======================================================================================================================

def main():
    with st.sidebar:
        selected = option_menu("事件驱动择时", ["策略回放", '策略回测'],
                               icons=['bi bi-play-circle', 'bi bi-reception-4'], menu_icon="bi bi-rocket-takeoff", default_index=0)
        files = st.file_uploader(label='上传策略文件：', type='json', accept_multiple_files=True)        

    if selected == "策略回放":
        replay(files)

    if selected == "策略回测":
        backtest(files)

if __name__ == '__main__':
    main()

    

    