# -*- coding: utf-8 -*-
"""
author: zengbin93
email: zeng_bin8888@163.com
create_dt: 2023/3/23 19:23
describe: 使用 DummyBacktest 进行快速回测迭代式研究
"""
import sys
sys.path.insert(0, '.')
sys.path.insert(0, '..')

import czsc
from czsc.connectors.mt5_connector import get_raw_bars
from czsc.strategies import CzscStrategyExample2


dummy = czsc.DummyBacktest(strategy=CzscStrategyExample2, read_bars=get_raw_bars,
                           signals_module_name='czsc.signals',
                           sdt='20200101', edt='20230301',
                           signals_path=r'/Users/admin/.czsc/signals',
                           results_path=r'/Users/admin/.czsc/results')

# 定义需要回测的品种，这里可以自定义
# symbols = get_symbols('A股主要指数')

# # 查看某个品种的交易回放
# dummy.replay(symbols[0])


# Python中的多进程必须在if __name__ == '__main__'中执行，否则会报错

if __name__ == '__main__':
    # 这里仅回测前10个品种，作为执行示例
    # czsc.empty_cache_path()
    #dummy.execute(symbols[:10], n_jobs=4)
    dummy.replay('EURUSD')

