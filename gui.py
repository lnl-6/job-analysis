#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图形界面入口（附加演示模块）。

与原脚本的关键差异：
    1. 启动时**不再弹 9 个 matplotlib 窗口**（原脚本 L297 在模块级跑分析 + 9 处 plt.show）
    2. 数据只在启动时加载一次，不在每次点击时重复读取
    3. 修正了「综合适配得分」的量纲问题（见 src/recommender.py）

用法：
    python gui.py
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import config, recommender  # noqa: E402
from src.cleaner import clean  # noqa: E402
from src.loader import DataLoadError, load_clean_ready  # noqa: E402


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.df = None
        self.info = None
        self._build_ui()
        self._load_data()

    # ---------------- 数据 ----------------
    def _load_data(self) -> None:
        try:
            df_raw, info = load_clean_ready()
            self.df, _ = clean(df_raw)
            self.info = info
            self._append(f"[加载成功] {info.describe()}")
            if info.is_demo:
                self._append(
                    "[注意] 当前是【合成演示数据】，结论不可用于真实引用。\n"
                    "       真实数据请放到 data/raw/大学生就业数据.csv\n"
                )
            cities = sorted(self.df[config.COL_CITY].dropna().unique().tolist())
            self._append(f"[可用城市] {', '.join(cities)}\n")
        except DataLoadError as exc:
            self.df = None
            self._append(f"[加载失败] {exc}\n")
            messagebox.showerror(
                "数据加载失败",
                f"{exc}\n\n请检查数据文件，或运行：\n"
                "python scripts/generate_demo_data.py",
            )

    # ---------------- 界面 ----------------
    def _build_ui(self) -> None:
        self.root.title("大学生就业城市推荐系统")
        self.root.geometry("760x680")
        self.root.resizable(False, False)

        tk.Label(self.root, text="📊 大学生就业城市智能推荐系统", font=("Microsoft YaHei", 16, "bold")).pack(pady=12)

        form = tk.Frame(self.root)
        form.pack(padx=20, fill="x")

        self.var_major = tk.StringVar()
        self.var_edu = tk.StringVar(value="本科")
        self.var_salary = tk.StringVar(value="8000")
        self.var_intern = tk.StringVar(value="是")

        rows = [
            ("所学专业：", tk.Entry(form, textvariable=self.var_major, width=30)),
            ("最高学历：", ttk.Combobox(form, textvariable=self.var_edu,
                                     values=["本科", "硕士", "博士"], state="readonly", width=27)),
            ("期望薪资(元)：", tk.Entry(form, textvariable=self.var_salary, width=30)),
            ("是否有实习经历：", ttk.Combobox(form, textvariable=self.var_intern,
                                       values=["是", "否", "不限"], state="readonly", width=27)),
        ]

        for i, (label, widget) in enumerate(rows):
            tk.Label(form, text=label, font=("Microsoft YaHei", 10)).grid(
                row=i, column=0, sticky="e", padx=(0, 8), pady=6
            )
            widget.grid(row=i, column=1, sticky="w", pady=6)

        btns = tk.Frame(self.root)
        btns.pack(pady=14)
        tk.Button(btns, text="🔍 开始城市推荐", command=self.on_recommend,
                  width=14, bg="#4CAF50", fg="white", font=("Microsoft YaHei", 10)).grid(row=0, column=0, padx=6)
        tk.Button(btns, text="🧹 清空结果", command=self.on_clear,
                  width=12, bg="#f44336", fg="white", font=("Microsoft YaHei", 10)).grid(row=0, column=1, padx=6)

        tk.Label(self.root, text="运行日志 / 推荐结果：", font=("Microsoft YaHei", 10)).pack(anchor="w", padx=20)
        self.text = scrolledtext.ScrolledText(self.root, width=88, height=22,
                                              font=("Consolas", 9), wrap="word")
        self.text.pack(padx=20, pady=(4, 16), fill="both", expand=True)

    # ---------------- 事件 ----------------
    def _append(self, msg: str) -> None:
        self.text.insert(tk.END, msg + "\n")
        self.text.see(tk.END)

    def on_clear(self) -> None:
        self.text.delete("1.0", tk.END)

    def on_recommend(self) -> None:
        if self.df is None:
            messagebox.showwarning("提示", "数据未加载成功，无法推荐。")
            return

        major = self.var_major.get().strip()
        if not major:
            messagebox.showwarning("提示", "请输入专业名称（支持模糊匹配，如「计算机」）。")
            return

        # 数值校验：修掉原脚本的裸 except
        try:
            salary = int(float(self.var_salary.get().strip()))
        except ValueError:
            messagebox.showwarning("提示", "期望薪资请输入数字，例如 8000。")
            return

        edu = self.var_edu.get()
        intern = "" if self.var_intern.get() == "不限" else self.var_intern.get()

        result = recommender.recommend(
            self.df,
            major=major,
            edu=edu,
            intern=intern,
            salary=salary,
        )

        self.text.insert(tk.END, "\n" + "=" * 60 + "\n")
        self._append(f"筛选条件：专业≈{major} ｜ 学历={edu} ｜ "
                     f"实习={self.var_intern.get()} ｜ 薪资={salary}±1000")
        self._append(recommender.format_recommendation(result, salary))


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
