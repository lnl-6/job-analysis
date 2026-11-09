# legacy/ —— 历史版本归档

## top_v0_original.py

这是项目**重构前**的原始脚本（411 行，单文件），保留作对照，说明重构解决了什么问题。

⚠️ **不要直接运行它**，它的问题已在新版本中修复：

| 原版问题 | 位置 | 新版本如何解决 |
|---|---|---|
| 启动时弹 9 个 matplotlib 窗口，阻塞 GUI | 9 处 `plt.show()`，且在模块级直接调用分析函数 | 改为 `savefig` 落盘，见 `src/viz.py` |
| 5 条结论写死成字符串 | 原 L287-291 | 改为从数据动态计算，见 `src/report.py` |
| 评分公式量纲不一致，权重失效 | 原 L381-385 | min-max 归一化后再加权，见 `src/recommender.py` |
| 裸 `except:` 吞掉所有异常 | 原 L353 | 改为 `except ValueError:` |
| 411 行全在一个函数里 | 全文 | 拆分为 loader / cleaner / analysis / viz / report |
| 图表不落盘，README 无图 | 全部 `plt.show()` | 统一输出到 `output/` |
| 无测试 | 无 | 新增 47 项测试（分析内核 27 / SQL 数据层 10 / 数据源适配 10：清洗不变量 / 统计性质 / 样本量门槛 / 报错信息 / 边界情况） |

查看现代码请从 [`main.py`](../main.py) 开始。
