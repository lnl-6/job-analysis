# 导入库
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import tkinter as tk
from tkinter import ttk, scrolledtext

# 图表中文配置
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

#==================== 你原始完整数据分析代码 完全未修改 ====================
def run_all_data_analysis():
    #-------------------步骤1：数据加载与初步探索
    # 3. 加载数据
    df = pd.read_csv("./大学生就业数据.csv")

    # 4. 初步探索（了解数据基本情况）
    print("数据形状：", df.shape)
    print("\n数据列名：", df.columns.tolist())
    print("\n缺失值统计：")
    print(df.isnull().sum())
    print("\n数值型数据统计：")
    print(df[["期望薪资", "满意度评分"]].describe())

    #-------------------步骤2：数据清洗（处理缺失值与异常值）
    # 1. 处理缺失值
    # 性别：用众数填充
    gender_mode = df["性别"].mode()[0]
    df["性别"] = df["性别"].fillna(gender_mode)

    # 期望薪资：用中位数填充（避免受异常值影响）
    salary_median = df["期望薪资"].median()
    df["期望薪资"] = df["期望薪资"].fillna(salary_median)

    # 是否有实习经历：用众数填充
    intern_mode = df["是否有实习经历"].mode()[0]
    df["是否有实习经历"] = df["是否有实习经历"].fillna(intern_mode)

    # 2. 处理异常值（以期望薪资为例）
    Q1 = df["期望薪资"].quantile(0.25)
    Q3 = df["期望薪资"].quantile(0.75)
    IQR = Q3 - Q1
    # 筛选正常范围数据
    df_clean = df[(df["期望薪资"] >= Q1-1.5*IQR) & (df["期望薪资"] <= Q3+1.5*IQR)]
    print(f"清洗前数据行数：{len(df)}")
    print(f"清洗后数据行数：{len(df_clean)}")  # 输出删除的异常值行数

    #-------------------步骤3：核心问题分析与可视化
    #-------问题1：学历对期望薪资和满意度的影响
    # 1. 按学历分组计算关键指标
    edu_analysis = df_clean.groupby("学历").agg({
        "期望薪资": ["mean", "count"],
        "满意度评分": "mean"
    }).round(2)  # 保留2位小数
    edu_analysis.columns = ["平均期望薪资", "人数", "平均满意度"]  # 重命名列
    print("="*50)
    print("学历分析结果：")
    print("="*50)
    print(edu_analysis)

    # 2. 可视化：学历与平均薪资的柱状图
    plt.figure(figsize=(8,7))
    edu_salary = edu_analysis["平均期望薪资"].reset_index()
    plt.bar(
        x=edu_salary["学历"],
        height=edu_salary["平均期望薪资"],
        color=["#63AFE0", "#AE91C9", "#4AD4A1"]
    )
    plt.xlabel("学历", fontsize=14)
    plt.ylabel("平均期望薪资（元）", fontsize=14)
    plt.title("不同学历毕业生的平均期望薪资", fontsize=16, pad=20)
    # 添加数值标签
    for i, v in enumerate(edu_salary["平均期望薪资"]):
        plt.text(i, v+50, f"{int(v)}", ha="center", fontsize=13)
    plt.show()

    # 3. 可视化：学历与满意度的柱状图
    plt.figure(figsize=(8,5))
    edu_satisfaction = edu_analysis["平均满意度"].reset_index()
    plt.bar(
        x=edu_satisfaction["学历"],
        height=edu_satisfaction["平均满意度"],
        color=["#FF8888", "#9F88FF", "#4D70C9"]
    )
    plt.xlabel("学历", fontsize=14)
    plt.ylabel("平均满意度评分", fontsize=14)
    plt.title("不同学历毕业生的平均满意度", fontsize=16, pad=20)
    for i, v in enumerate(edu_satisfaction["平均满意度"]):
        plt.text(i, v+0.1, f"{v}", ha="center", fontsize=13)
    plt.ylim(0, 5)  # 固定y轴范围（0-5，符合评分标准）
    plt.show()

    #-------问题2：实习经历对期望薪资的影响
    # 1. 按实习经历分组计算薪资
    intern_salary = df_clean.groupby("是否有实习经历")["期望薪资"].agg(["mean", "count"]).round(2)
    intern_salary.columns = ["平均期望薪资", "人数"]
    print("="*50)
    print("实习经历与薪资关系：")
    print("="*50)
    print(intern_salary)

    # 2. 可视化：箱线图（展示薪资分布与异常值）
    plt.figure(figsize=(8,5))
    sns.boxplot(
        x="是否有实习经历",
        y="期望薪资",
        data=df_clean,
        palette="Set2",
        legend=False
    )
    plt.xlabel("是否有实习经历", fontsize=14)
    plt.ylabel("期望薪资（元）", fontsize=14)
    plt.title("实习经历对期望薪资分布的影响", fontsize=16)
    plt.show()

    #-------问题3：不同专业/毕业去向的薪资分布
    # 1. 专业与薪资分析（取平均薪资前5的专业）
    major_salary = df_clean.groupby("专业")["期望薪资"].mean().sort_values(ascending=False).head(5).reset_index()
    print("="*50)
    print("平均薪资前5的专业：")
    print("="*50)
    print(major_salary)

    # 可视化专业薪资
    plt.figure(figsize=(18,10))
    plt.bar(
        x=major_salary["专业"],
        height=major_salary["期望薪资"],
        color="#ffbb78"
    )
    plt.xlabel("专业", fontsize=14)
    plt.ylabel("平均期望薪资（元）", fontsize=14)
    plt.title("平均期望薪资前5的专业", fontsize=16)
    plt.xticks(rotation=45, fontsize=13, ha='right')
    plt.yticks(fontsize=13)
    for i, v in enumerate(major_salary["期望薪资"]):
        plt.text(i, v+200, f"{int(v)}", ha="center", fontsize=13)
    plt.show()

    # 2. 毕业去向与薪资分析
    direction_salary = df_clean.groupby("毕业去向")["期望薪资"].agg(["mean", "count"]).round(2)
    direction_salary.columns = ["平均期望薪资", "人数"]
    print("="*50)
    print("毕业去向与薪资关系：")
    print("="*50)
    print(direction_salary)

    # 可视化毕业去向薪资
    plt.figure(figsize=(18,10))
    direction_salary_plot = direction_salary["平均期望薪资"].reset_index()
    plt.bar(
        x=direction_salary_plot["毕业去向"],
        height=direction_salary_plot["平均期望薪资"],
        color="#99BBFF"
    )
    plt.xlabel("毕业去向", fontsize=14)
    plt.ylabel("平均期望薪资（元）", fontsize=14)
    plt.title("不同毕业去向的平均期望薪资", fontsize=16)
    plt.yticks(fontsize=13)
    plt.xticks(rotation=45, fontsize=13, ha='right')
    for i, v in enumerate(direction_salary_plot["平均期望薪资"]):
        plt.text(i, v+200, f"{int(v)}", ha="center", fontsize=13)
    plt.show()

    #不同毕业去向的期望薪资分布箱线图
    plt.figure(figsize=(18, 10))
    sns.boxplot(
        x="专业",
        y="期望薪资",
        data=df_clean,
        palette="Set2",
        legend=False
    )
    plt.xlabel("专业", fontsize=14)
    plt.ylabel("期望薪资（元）", fontsize=14)
    plt.title("不同专业对期望薪资分布的影响", fontsize=16)
    plt.xticks(rotation=45, fontsize=13, ha='right')
    plt.yticks(fontsize=13)
    # 计算所有专业的平均薪资
    all_major_salary = df_clean.groupby("专业")["期望薪资"].mean().reset_index()
    # 给所有专业添加标签
    for i, v in enumerate(all_major_salary["期望薪资"]):
        plt.text(i, v + 200, f"{int(v)}", ha="center", fontsize=13)
    plt.show()

    #-------问题4：工作城市与期望薪资的关系
    # 1. 筛选主要城市（排除“无”和“其他”，取人数前6的城市）
    main_cities = df_clean[df_clean["工作城市"].isin(["上海","北京","广州","深圳","武汉","南京"])]
    city_salary = main_cities.groupby("工作城市").agg({
        "期望薪资": ["mean", "count"]
    }).round(2)
    city_salary.columns = ["平均期望薪资", "人数"]
    city_salary = city_salary.sort_values("平均期望薪资", ascending=False)
    print("="*50)
    print("主要城市薪资分析：")
    print("="*50)
    print(city_salary)

    # 2. 可视化城市薪资
    plt.figure(figsize=(12,8))
    plt.bar(
        x=city_salary.index,
        height=city_salary["平均期望薪资"],
        color="#77DDFF"
    )
    plt.xlabel("工作城市", fontsize=14)
    plt.ylabel("平均期望薪资（元）", fontsize=14)
    plt.title("主要工作城市的平均期望薪资", fontsize=16)
    for i, v in enumerate(city_salary["平均期望薪资"]):
        plt.text(i, v+100, f"{int(v)}", ha="center", fontsize=13)
    plt.show()

    #-------问题五:自设问题
    # 1. 同专业下不同性别的期望薪资对比
    major_gender_salary = df_clean.groupby(["专业", "性别"])["期望薪资"].mean().unstack()
    major_gender_salary = major_gender_salary.dropna()
    major_gender_salary = major_gender_salary.sort_values(by="男", ascending=False)
    print("="*50)
    print("各专业男女期望薪资均值对比：")
    print("="*50)
    print(major_gender_salary.round(2))

    #可视化：男女期望薪资对比双折线图
    plt.figure(figsize=(14, 7))
    plt.plot(
        major_gender_salary.index,
        major_gender_salary["男"],
        color="#1f77b4",
        marker="o",
        linewidth=2.5,
        markersize=8,
        label="男性期望薪资"
    )
    plt.plot(
        major_gender_salary.index,
        major_gender_salary["女"],
        color="#ff69b4",
        marker="s",
        linewidth=2.5,
        markersize=8,
        label="女性期望薪资")
    for i, v in enumerate(major_gender_salary["男"]):
        plt.text(i, v + 20, f"{round(v)}", ha="center", fontsize=10)
    for i, v in enumerate(major_gender_salary["女"]):
        plt.text(i, v -35, f"{round(v)}", ha="center", fontsize=10)
    plt.xlabel("专业", fontsize=14)
    plt.ylabel("期望薪资均值", fontsize=14)
    plt.title("各专业男女期望薪资均值对比", fontsize=16, pad=20)
    plt.xticks(rotation=45, ha="right",fontsize=13)
    plt.yticks(fontsize=13)
    plt.legend(fontsize=12)
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()

    # 2. 不同毕业去向毕业生就业满意度对比
    print("="*50)
    print("不同毕业去向毕业生就业满意度————统计运算结果")
    print("="*50)
    satisfaction_analysis = df_clean.groupby("毕业去向")["满意度评分"].agg(
        人数="count",
        平均满意度="mean",
        中位数满意度="median",
        标准差="std").round(2)
    print(satisfaction_analysis)

    plt.figure(figsize=(18, 10))
    sns.boxplot(
        x="毕业去向",
        y="满意度评分",
        data=df_clean,
        palette=["#FF8888", "#FFDD55", "#BBFF66", "#77FFEE", "#99BBFF","#FF77FF"]
    )
    plt.xlabel("毕业去向", fontsize=14)
    plt.ylabel("就业满意度评分（0-5分）", fontsize=14)
    plt.title("不同毕业去向毕业生就业满意度分布对比", fontsize=16, pad=20)
    plt.xticks(rotation=45, ha='right', fontsize=13)
    plt.yticks(fontsize=13)
    plt.ylim(0, 5)
    plt.tight_layout()
    plt.show()

    print("="*50)
    print("毕业生数据分析项目核心结论")
    print("="*50)
    print("1. 学历影响：学历越高，平均期望薪资越高（博士>硕士>本科）；满意度评分博士最高。")
    print("2. 实习经历影响：有实习经历的毕业生平均期望薪资（约7200元）高于无实习经历者（约6500元）。")
    print("3. 专业差异：平均薪资前3的专业为临床医学、计算机、电子信息，均为技术/医疗类专业。")
    print("4. 毕业去向：选择“就业”的毕业生平均期望薪资最高，“考研”群体薪资期望最低。")
    print("5. 城市差异：一线城市（上海、北京、深圳）平均薪资显著高于新一线城市（武汉、南京）。")
    print("="*50)
    
    return df_clean

# 提前加载干净数据
df_clean = run_all_data_analysis()

#==================== GUI窗口（新增性别选项 + 多按钮） ====================
def create_gui():
    root = tk.Tk()
    root.title("大学生就业城市推荐系统")
    root.geometry("650x600")

    # 标题
    tk.Label(root, text="📊 大学生就业城市智能推荐系统", font=("黑体", 18)).pack(pady=15)

    # 1 专业
    tk.Label(root, text="所学专业：", font=("宋体",11)).place(x=60, y=70)
    entry_major = tk.Entry(root, width=35, font=("宋体",11))
    entry_major.place(x=160, y=70)

    # 2 学历
    tk.Label(root, text="最高学历：", font=("宋体",11)).place(x=60, y=110)
    combo_edu = ttk.Combobox(root, width=32, font=("宋体",11))
    combo_edu["values"] = ("本科", "硕士", "博士")
    combo_edu.place(x=160, y=110)
    combo_edu.current(0)

    # 3 性别 新增男女选项
    tk.Label(root, text="性别：", font=("宋体",11)).place(x=60, y=150)
    combo_gender = ttk.Combobox(root, width=32, font=("宋体",11))
    combo_gender["values"] = ("男", "女")
    combo_gender.place(x=160, y=150)
    combo_gender.current(0)

    # 4 期望薪资
    tk.Label(root, text="期望薪资(数字)：", font=("宋体",11)).place(x=60, y=190)
    entry_salary = tk.Entry(root, width=35, font=("宋体",11))
    entry_salary.place(x=160, y=190)

    # 5 是否有实习经历
    tk.Label(root, text="是否有实习经历：", font=("宋体",11)).place(x=60, y=230)
    combo_intern = ttk.Combobox(root, width=32, font=("宋体",11))
    combo_intern["values"] = ("是", "否")
    combo_intern.place(x=160, y=230)
    combo_intern.current(0)

    # 结果文本框
    tk.Label(root, text="推荐结果：", font=("宋体",12)).place(x=60, y=270)
    result_text = scrolledtext.ScrolledText(root, width=70, height=12, font=("宋体",10))
    result_text.place(x=60, y=295)

    # 城市推荐函数（加入性别筛选）
    def btn_recommend():
        result_text.delete(1.0, tk.END)
        major = entry_major.get().strip()
        if not major:
            result_text.insert(tk.END, "请输入专业名称！\n")
            return
        try:
            salary = int(entry_salary.get().strip())
        except:
            result_text.insert(tk.END, "薪资请输入纯数字！\n")
            return

        edu = combo_edu.get()
        gender = combo_gender.get()
        intern = combo_intern.get()

        # 多条件筛选：专业+学历+性别+实习+薪资
        filter_df = df_clean[df_clean["专业"].str.contains(major, na=False, case=False)]
        filter_df = filter_df[filter_df["学历"] == edu]
        filter_df = filter_df[filter_df["性别"] == gender]
        filter_df = filter_df[filter_df["是否有实习经历"] == intern]

        salary_low = salary - 1000
        salary_high = salary + 1000
        filter_df = filter_df[(filter_df["期望薪资"] >= salary_low) & (filter_df["期望薪资"] <= salary_high)]

        if filter_df.empty:
            result_text.insert(tk.END, "暂无匹配数据，请调整条件重试！\n")
            return

        city_stat = filter_df.groupby("工作城市").agg(
            就业人数=("满意度评分", "count"),
            平均满意度=("满意度评分", "mean"),
            平均期望薪资=("期望薪资", "mean")
        ).reset_index()

        city_stat["综合适配得分"] = (
            city_stat["就业人数"] * 0.5
            + city_stat["平均满意度"] * 0.3
            + city_stat["平均期望薪资"] / 1000 * 0.2
        )
        city_stat = city_stat.sort_values("综合适配得分", ascending=False)
        top5 = city_stat.head(5)

        result_text.insert(tk.END, "===== 适配度TOP5推荐城市 =====\n\n")
        for idx, row in top5.iterrows():
            info = f"推荐城市：{row['工作城市']}\n" \
                   f"同条件就业人数：{row['就业人数']} 人\n" \
                   f"平均满意度：{row['平均满意度']:.2f} 分\n" \
                   f"平均薪资：{row['平均期望薪资']:.0f} 元\n" \
                   f"综合适配得分：{row['综合适配得分']:.2f}\n" \
                   "----------------------------\n"
            result_text.insert(tk.END, info)

    # 清空结果
    def clear_result():
        result_text.delete(1.0, tk.END)

    # 按钮布局 多出多个选项按钮
    tk.Button(root, text="🔍 开始城市推荐", command=btn_recommend, width=12, bg="#4CAF50", fg="white").place(x=80, y=480)
    tk.Button(root, text="📊 运行原数据分析", command=run_all_data_analysis, width=14, bg="#2196F3", fg="white").place(x=220, y=480)
    tk.Button(root, text="🧹 清空结果", command=clear_result, width=10, bg="#f44336", fg="white").place(x=380, y=480)

    root.mainloop()

if __name__ == "__main__":
    create_gui()