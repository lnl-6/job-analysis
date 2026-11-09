# -*- coding: utf-8 -*-
"""数据源适配包。

每个数据源一个模块，职责只有「把该源的原始记录映射到项目既有的分析口径」：
    - `src.sources.boss` —— Boss 直聘岗位数据（调控径见 docs/data_source_boss.md）
抓取脚本不放这里（抓取属于采集，见 scripts/），统计与呈现也不放这里。
"""