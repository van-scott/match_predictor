# -*- coding: utf-8 -*-
"""Builds prompts for AI prediction."""


def build_analysis_prompt(home: str, away: str, league: str, match_time: str, ho: str, do_: str, ao: str, prob_str: str) -> str:
    return f"""你是一位专业足球分析师。请对以下足球比赛进行深度分析预测：

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【比赛信息】
对阵：{home} vs {away}
联赛：{league}
比赛时间：{match_time}
当前赔率：主胜 {ho} | 平局 {do_} | 客胜 {ao}
赔率隐含概率：{prob_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【⚠️ 核心指令：格式与术语标准】
你必须完全按照以下格式结构返回分析报告，不能遗漏任何一节，不能更改任何小标题，注意保留所有的连字符 `---` 以及 emoji 符号，使得输出极其专业精美。

### 重要说明（关于让球与不让球赔率）：
- 如果输入中缺少某些赔率，或者没有指明“让球盘口”与“让球赔率”，请作为专家根据“不让球赔率”与两队实力差距，合理估算并补齐。
- 让球盘口应以让球个数表示（例如 -1.0 球 / +1.0 球）。
- 让球赔率应当符合精算概率。

请生成以下格式的内容（直接返回 Markdown）：

### {home} vs {away} 比赛分析报告

比赛信息：
* 联赛：{league}
* 比赛时间：{match_time}
* 赔率：主胜 {ho} | 平局 {do_} | 客胜 {ao}
* 让球盘口：[填写合理让球数]
* 让球赔率：让球主胜 [x] | 让球平局 [x] | 让球客胜 [x]
* 赔率隐含概率：{prob_str}

---

📊 一、综合形势分析
1. 赔率隐含概率分析
2. 近期状态（主客队）
3. 历史交锋
4. 主客场表现
综合判断

---

🎯 二、胜平负预测
* 推荐结果：主胜/平局/客胜
* 置信度：高/中/低
* 核心理由（3条）

---

⚽ 三、比分预测
* 最可能比分 + 理由
* 备选比分1 + 理由
* 备选比分2 + 理由
* 总进球区间 + 理由

---

⏱️ 四、半全场预测
* 半场 + 理由
* 全场 + 理由

---

💰 五、投注价值
* 不让球盘口建议 + 理由
* 让球盘口建议 + 理由

---

⚠️ 六、风险提示
* 主队风险
* 客队风险
* 突发伤病/红牌风险
"""


def calc_implied_probability_str(ho: str, do_: str, ao: str) -> str:
    try:
        ho_f = float(ho)
        do_f = float(do_)
        ao_f = float(ao)
        sum_inv = (1 / ho_f) + (1 / do_f) + (1 / ao_f)
        home_prob = (1 / ho_f) / sum_inv * 100
        draw_prob = (1 / do_f) / sum_inv * 100
        away_prob = (1 / ao_f) / sum_inv * 100
        return f"主胜 {home_prob:.1f}% | 平局 {draw_prob:.1f}% | 客胜 {away_prob:.1f}%"
    except Exception:
        return "主胜 40.7% | 平局 35.7% | 客胜 36.5%"
