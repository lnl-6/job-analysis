# 方法学、踩坑与局限

> 这份文档原本是 README 的后半部分，为了让首页只讲「干什么、怎么用」而搬到这里。
> 内容包括：数字是怎么算出来的、这类项目最容易踩的坑、已知局限与待办。

## 一、结果从哪来（本仓库不预置结论）

**这里不写任何具体数字**——写了就会变成「与数据脱钩的预设结论」。跑一条命令，结果全部落在报告里：

```bash
python main.py          # → output/report.md
```

报告里每个数字都能追溯到「由什么算出」：

| 报告里的内容 | 由什么算出 |
|---|---|
| 数据指纹（SHA-256 前 12 位）/ 规模 / 阈值 / 环境版本 / 生成时间 | `src/report.py::provenance_lines` |
| 样本量 / 均值 / 中位数 / 均值的 95% 置信区间 | `src/stats.py::describe` |
| 两组均值差 + 95% 置信区间 | `src/stats.py::compare`（Welch–Satterthwaite 自由度） |
| Welch t 检验、Mann-Whitney U 检验、Cohen's d | `src/stats.py::compare`（scipy 实现） |
| 「差异显著 / 不显著 / 样本不足」的判定 | `src/stats.py::Comparison.verdict`（判定只有这一处，报告不另下判断） |
| 某维度对指标的「组间解释力」eta² 与 ANOVA p 值 | `src/quality.py::group_effect` |
| 缺失率 / 重复行 / 取值集中度 / 薪资分布形态 | `src/quality.py::audit` |
| 多组对比的 Holm 校正后 p 值 | `src/stats.py::holm_adjust`（同一次运行的所有对比算一个检验族） |
| 城市排序的稳健性（换权重的 Spearman 秩相关） | `src/recommender.py::weight_sensitivity` |

两条自证机制（不是嘴上说说，CI 都会跑）：

- **样本量门槛**：任一组低于 `config.MIN_GROUP_SIZE`（默认 30）只报样本量、不给差异结论；交叉表里样本不足的格子直接留空（实测样本量单独列表）。
- **门禁的假阳性防线**：所有阈值（样本量门槛、显著性水平、IQR 系数）集中在 `src/config.py`，报告里每条判定都带样本量；门禁公式写在 SQL 里，由 `tests/test_sql_layer.py::test_sql_constants_match_config` 盯着，不让它和 Python 侧悄悄分叉。

图表同样是运行产物（`output/` 不入库；README 引用的这份在 `docs/images/`，属示例输出，随数据/参数变化）：

![专业城市热力图](docs/images/09_专业城市_薪资热力图.png)

---

## 二、踩坑记录

真实踩过的坑，按遇到顺序记录。第 1~5 条发生在重构阶段；第 6 条是把代码真正跑一遍才踩出来的；第 7 条是「能不能经得起追问」这一关暴露出来的——数据可以换，但结论不能是预设的。

### 1. matplotlib 的中文显示成方块

**现象**：图里所有中文标题、坐标轴标签都变成 □□□□。
**原因**：matplotlib 默认字体不含中文字形。
**解决**：显式指定中文字体，并关闭 `unicode_minus`（否则负号也会变方块）。

```python
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False
```

**后续改进**：直接写死 `SimHei` 在 macOS / Linux 上会失效。改为**按优先级探测已安装字体**（见 `src/config.py::setup_chinese_font`），Windows / macOS / Linux 都能跑。

### 2. 9 个 `plt.show()` 把 GUI 卡死

**现象**：点开程序先弹 9 个图表窗口，必须全部手动关掉，界面才出来。
**原因**：`plt.show()` 是**阻塞调用**，且在模块级直接跑了分析函数。
**解决**：全部改成 `savefig` 落盘 + `plt.close(fig)` 释放内存；数据加载与分析只在启动时做一次。图表通过 README / 报告查看，而不是弹窗。

### 3. 「综合适配得分」权重形同虚设

**原始公式**：
```python
score = 就业人数*0.5 + 平均满意度*0.3 + 平均期望薪资/1000*0.2
```

**问题**：三个指标量纲差了一个数量级以上——就业人数可能 0~100，满意度只有 0~5，薪资除 1000 后约 5~20。直接加权，**「人数」会压倒性主导，0.5/0.3/0.2 只写在代码里好看，不影响排序**。

**解决**：先对每个指标做 min-max 归一化到 `[0,1]`，再加权求和。这样权重才真正代表「我更看重哪个维度」。

相关代码：`src/recommender.py::recommend`

### 4. 结论被写死在代码里

**原始写法**：分析跑完后，直接 `print` 几行固定的结论字符串。
**问题**：数据一换，结论还是那几句——这是「伪分析」，任何一次数据更新都会立刻穿帮。
**解决**：所有结论由 `report.build_conclusions()` **从 DataFrame 实时算出**。数据变了，结论自动变。

> 同一个坑还踩了第二次：报告里的「专业维度（薪资前 5）」一度也是写死的标题，跑 `--top 8` 时表里有 8 行、标题却写着前 5。现在标题由结果长度推导（`len(results["major_top"])`），CI 里也加了断言防止回退。

### 5. 编码问题导致中文列名读不出来

**现象**：`KeyError: '性别'`。
**原因**：CSV 存成了 GBK，而 `read_csv` 默认按 UTF-8 解析。
**解决**：加 `encoding='utf-8-sig'`（兼容 Excel 导出的 BOM），并在 `UnicodeDecodeError` 时回退到 `gbk`。

### 6. Windows 中文控制台把整个流程跑崩

**现象**：在 Windows 的 cmd / PowerShell（默认代码页 GBK）里跑 `python main.py`，图表全画完了，`[5/5] 生成报告` 却一直没出现——报告根本没落地：

```
Traceback (most recent call last):
  ...
  File "src\report.py", line 243, in print_console_report
    print("⚠️  当前使用的是【合成演示数据】，结论不可用于真实引用！")
UnicodeEncodeError: 'gbk' codec can't encode character '\u26a0' in position 0: illegal multibyte sequence
```

**原因**：控制台打印的字符串里带了 `⚠️`（U+26A0）。中文在 GBK 里编得出来（列名、城市名、图表文件名都没事），**emoji 编不出来**；而 `print` 抛出的编码异常会直接终止进程，不像写文件那样容易被忽略。分析、图表全算完了，最后一步写报告却没执行。

**为什么 CI 没发现**：GitHub Actions 的 Linux runner 一律 UTF-8，这条路径永远走不到。这类 bug 只有「在目标平台真的跑一次」才会暴露。

**解决**：两件事——

1. 控制台输出不再用 emoji，改成纯文本标记（`src/report.py:243` 的 `⚠️` → `[!]`）；报告 markdown 里的 `⚠️` 保留（写文件是 UTF-8，GitHub 上渲染正常）。
2. 入口加编码兜底，万一以后又混进编不出来的字符，只是显示成 `?`，而不是让整轮分析白跑：

```python
# main.py::make_console_safe()
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass  # 被重定向/包装过的流没有 reconfigure，忽略即可
```

**回归测试**：CI 里补了一步等价复现——在 Linux 上强制按 GBK 编码跑一遍完整流程：

```bash
PYTHONIOENCODING=gbk python main.py --top 8
```

### 7. 「分析结论」其实是生成器常数的回声

**现象**：报告看着很专业——学历、城市、专业三组差异都「显著」，方向也符合直觉。但那是**必然**的：演示数据的薪资就是按 `EDU_BASE_SALARY × CITY_MULTIPLIER × MAJOR_MULTIPLIER` 构造出来的，结论等于把脚本里的常数表再念一遍。用这种数据讲「我发现学历影响薪资」，被追问一句「你怎么证明不是你写进去的」就答不上来。

**解决**（把「预设」和「测量」分开）：

1. **参数全部公开**：模拟数据的所有系数与注入比例登记在 [`data/README.md`](data/README.md)，并可用 `--show-params` 打印——它是**数据说明**，不是结论。
2. **结论只由测量决定**：新增 `src/stats.py`，报告只输出「均值差 / 95% 置信区间 / p 值 / 效应量 / 判定」，判定逻辑只有一处（`Comparison.verdict`）。
3. **样本不足不给结论**：门槛 30 条集中在 `src/config.py`，报告如实打印；交叉表小样本格子留空，样本量另表列出。
4. **加 null 对照场景**：把所有分组效应压成中性值再跑同一套检验——能在这个场景上「发现」差异，才说明管线有假阳性，CI 会跑这个场景。
5. **README 不复述数字**：README 里不出现任何一次运行的具体结果，就不会有与数据脱钩的预设结论；`tests/test_pipeline.py::test_readme_keeps_no_run_specific_numbers` 会拦住回退。

**顺带修掉的**：权重敏感性没人管（换一组权重重算，看 TOP1 会不会翻）、交叉表里 1 个样本的均值也会被当成「发现」、「综合适配得分」的文案把权重写死成 0.5/0.3/0.2。

> 一句话：**数据可以是模拟的，结论必须是测出来的。**

### 8. 换真实数据后暴露的三个 bug（只有真的跑才会出现）

**现象**：演示数据上测试全绿、全流程跑通；换成 1 万行真实数据后立刻炸出三件事——而且都不是「数据脏」的错，是代码的错。

1. **`AttributeError: 'numpy.ndarray' object has no attribute 'quantile'`**：分位数工具内部把序列清洗成了 numpy 数组，却仍按 pandas 的 `.quantile()` 调用——`src/stats.py::quantiles` 改成 `np.quantile`。演示数据上没暴露，是因为那条分支只在「分组样本量达到门槛」时才会走到。
2. **`ValueError: Encountered all NA values`**：pandas 3 的 `stack()` 默认**保留**缺失值，交叉表里全空的行列会把 `idxmax()` 顶炸——先过滤 `notna()` 再取极值。同一份代码在 pandas 2 上是好的，这是升级到 pandas 3 才会遇到的坑。
3. **门禁自己误报**：`ODS 行数 = DWD 行数` 这条检查，在清洗本来就会剔除无效薪资/异常值的前提下必然失败（真实数据里有 `-2100` 这类无效值）。修法是让检查把剔除行数算进去——`ODS = DWD + rows_invalid_salary + rows_outlier_removed`——并且所有检查只看**最新一次装载**（新增 `v_latest_run` 视图），否则历史批次的脏数据会让门禁永远红着。

**教训**：演示数据是「按剧本生成的好数据」，它证明不了代码健壮，**真实数据的第一轮运行才是真正的集成测试**。顺带定了个规矩：门禁的公式与阈值写在 SQL 里，也要被测试盯住（`tests/test_sql_layer.py::test_sql_constants_match_config`），不然门禁自己会变成新的坑。

### 9. 第二次装载就崩：DROP TABLE 的顺序被外键挡住

**现象**：第一次装载一切正常；同一台机器上再装一次，直接失败：

```
[错误] IntegrityError: FOREIGN KEY constraint failed
```

**原因**：schema 脚本按「ODS → 维表 → 事实表 → 结果表」的阅读顺序写，`DROP TABLE` 也就跟着这个顺序——但事实表有外键指向维表，**删被引用的父表时数据库会拦住**：SQLite（`foreign_keys=ON`）报 `FOREIGN KEY constraint failed`，MySQL 报 `errno 3730: Cannot drop table referenced by a foreign key constraint`。

**为什么 CI 与测试都没抓到**：SQL 层测试用内存库，`ensure_schema` 连续调两次时表是空的、外键检查无事可做；CI 的 MySQL 是全新容器，也只装载一次。**「先装载、再重新引导 schema」这条路没人走过**。

**解决**：两个 schema 文件都在最前面加一段「按子表 → 父表顺序清场」的 DROP 块（视图与查询不受影响），并补一条回归测试——**先装载一次，再重新引导 schema，然后照常装载**（`test_rebootstrap_after_load_is_safe`）。这条测试修的就是「只有第二次运行才会出现」的 bug。

---

### 10. 换数据源最容易踩的坑：口径漂移

把「问卷」换成「Boss 岗位」时，**列名不变但语义变了**：`期望薪资` 从「受访者期望月薪」变成「岗位薪资区间中值」，`满意度评分` 从「受访者满意度」变成「岗位吸引力代理分」。

危险之处在于：代码一行不改也能跑通，报告数字看起来依旧「合理」，但已经把「岗位挂牌薪资」讲成了「毕业生期望薪资」——这不是 bug，是**统计口径造假**。

处理办法是四件事一起做：

1. 列名保持不动（改名会连带改动 loader / cleaner / analysis / report / viz / SQL / BI，代价远大于收益）；
2. 报告溯源区打印「数据口径」一行（`config.SOURCE_NOTES` + `report.provenance_lines`），每份报告自带口径声明；
3. 口径文档 [`docs/data_source_boss.md`](docs/data_source_boss.md) 写全字段映射、薪资解析、代理分公式与已知限制；
4. 测试守住：`tests/test_source_boss.py::test_sample_dataset_runs_through_pipeline` 断言溯源里必须出现「数据口径」与「岗位薪资区间中值」，删掉声明就红。

**教训**：换数据源不是换一个 CSV 路径，是换一套「这些列到底在测什么」。能被写进文档、能被报告打印、能被测试拦住的口径，才不算漂移。

---

## 三、局限

这份分析是**描述性统计**，请注意：

- 只说明「相关」，**不能推出因果**。例如「有实习的人薪资更高」不等于「实习导致了高薪」，也可能是家庭资源/专业本身带来的选择偏差。
- 若样本不是随机抽样，结论**不可外推到总体**。
- IQR 剔除会去掉一部分高端样本（剔除多少行由当次数据决定，见每次运行报告的「数据清洗」一节——报告不复述固定比例），会让平均薪资偏低。
- **不做预测**：本项目不含趋势外推、不含个体结果预测、也不含「该去哪」的建议；「城市排序」只是对已收集样本的描述性排序。
- **换数据源不改变推断的性质，但会改变结论的适用范围**：岗位数据是「在招岗位快照」，`n` 条岗位 ≠ `n` 个人；薪资是挂牌区间中值，不是到手薪资；「岗位吸引力代理分」不是满意度。这些边界逐条写在 [`docs/data_source_boss.md`](docs/data_source_boss.md) 第 7 节。

---
