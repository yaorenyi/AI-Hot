#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI HOT 日报 → 单文件 HTML 晨报仪表盘生成器

用法:
    python build.py                # 拉最新日报，生成 output/index.html + output/YYYY-MM-DD.html
    python build.py --date 2026-10-07
    python build.py --out dist

只依赖 Python 标准库,无需 pip install。
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API_BASE = "https://aihot.virxact.com/api/v1"
CANONICAL = "https://aihot.news/daily"
UA = "aihot-skill/1.2.1 (+https://aihot.virxact.com/aihot-skill/)"

# 五个固定版块,顺序即展示顺序
SECTIONS = [
    ("sec-model", "模型发布 / 更新"),
    ("sec-product", "产品发布 / 更新"),
    ("sec-industry", "行业动态"),
    ("sec-paper", "论文研究"),
    ("sec-tips", "技巧与观点"),
]

# 快讯归类关键词。每项为 (关键词, 权重),按版块顺序独立打分,得分最高者胜。
# 权重设计:强特征词(产品形态词、资本市场词、论文体裁词)给 2 分,通用词给 1 分。
SECTION_KEYWORDS = {
    "sec-model": [
        ("模型", 2), ("权重", 2), ("开源模型", 2), ("参数", 2), ("架构", 2),
        ("微调", 2), ("蒸馏", 2), ("量化", 2), ("moe", 2), ("多模态", 2),
        ("预训练", 2), ("激活参数", 2), ("评测", 1), ("基准", 1), ("推理", 1),
        ("benchmark", 1), ("嵌入", 2), ("embedding", 2), ("开源", 2),
        ("gpt", 2), ("claude", 2), ("gemini", 2), ("deepseek", 2), ("llama", 2),
        ("qwen", 2), ("mistral", 2), ("grok", 2), ("kimi", 2), ("llm", 2),
        ("arc-agi", 2), ("openrouter", 1), ("huggingface", 2),
    ],
    "sec-product": [
        ("插件", 2), ("app", 2), ("应用", 2), ("客户端", 2), ("工作流", 2),
        ("集成", 2), ("扩展", 2), ("sdk", 2), ("api", 2), ("命令行", 2),
        ("cli", 2), ("浏览器", 2), ("ide", 2), ("编码", 2), ("coding", 2),
        ("ios", 2), ("android", 2), ("桌面端", 2), ("workspace", 2),
        ("助手", 2), ("服务", 1), ("平台", 1), ("工具", 1), ("上线", 2),
        ("公测", 2), ("内测", 2), ("开放", 1), ("发布", 1), ("更新", 1),
        ("版本", 1), ("支持", 1), ("功能", 1), ("git", 2), ("开发", 1),
        ("会议", 1), ("文档", 1), ("表格", 1), ("搜索", 1), ("订阅", 1),
        ("虚拟机", 2), ("vm", 1), ("部署", 2), ("容器", 2),
        ("cowork", 2), ("远程控制", 2), ("云端", 1), ("会话", 1), ("协议", 1),
    ],
    "sec-industry": [
        ("融资", 2), ("投资", 2), ("估值", 2), ("上市", 2), ("ipo", 2),
        ("收购", 2), ("并购", 2), ("裁员", 2), ("财报", 2), ("营收", 2),
        ("利润", 2), ("支出", 2), ("基金", 2), ("股东", 2), ("法院", 2),
        ("诉讼", 2), ("裁定", 2), ("监管", 2), ("政策", 2), ("法案", 2),
        ("禁令", 2), ("合规", 2), ("版权", 2), ("市场", 1), ("公司", 1),
        ("合作", 1), ("签约", 1), ("用户", 1), ("增长", 1), ("份额", 1),
        ("算力", 2), ("芯片", 2), ("数据中心", 2), ("供应链", 2),
        ("安全", 1), ("泄露", 2), ("事故", 2), ("组织", 1), ("条款", 1),
        ("亿元", 2), ("亿美元", 2), ("gdp", 2), ("政策风险", 2),
    ],
    "sec-paper": [
        ("论文", 2), ("paper", 2), ("arxiv", 2), ("预印本", 2), ("期刊", 2),
        ("证明", 2), ("定理", 2), ("推导", 2), ("形式化", 2), ("数据集", 2),
        ("研究", 1), ("实验", 1), ("评估", 1), ("评测", 1), ("基准", 1),
        ("综述", 2), ("方法", 1), ("框架", 1), ("对齐", 1), ("可解释", 1),
        ("鲁棒", 1), ("数学", 2), ("引用协议", 2), ("学者", 2), ("团队提出", 2),
    ],
    "sec-tips": [
        ("技巧", 2), ("指南", 2), ("教程", 2), ("实践", 2), ("心得", 2),
        ("解读", 2), ("观点", 2), ("复盘", 2), ("推荐", 1), ("用法", 2),
        ("提示词", 2), ("prompt", 2), ("避坑", 2), ("上手", 2), ("玩法", 2),
        ("盘点", 2), ("观察", 1), ("建议", 1), ("最佳实践", 2), ("案例", 1),
        ("实战", 2), ("总结", 1), ("如何", 1),
    ],
}

SUMMARY_MAX = 60  # 中文摘要最大字数


# ---------------------------------------------------------------- HTTP

def fetch(path, timeout=20):
    """GET JSON,返回 dict。失败抛异常。"""
    url = API_BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_daily(date_str=None):
    """
    拉日报。先试指定日期(或今天),404 时回退到最近一期。
    返回 (report_dict, 是否为回退)。
    """
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    target = date_str or today
    try:
        data = fetch("/dailies/%s" % target)
        return data["report"], False
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        print("[!] %s 日报尚未生成,回退到最近一期" % target)
    except Exception as e:
        print("[!] 请求 %s 失败(%s),回退到最近一期" % (target, e))

    # 回退:只查一次有界索引
    idx = fetch("/dailies?limit=7")
    entries = idx.get("dailies") or idx.get("items") or idx.get("entries") or []
    if not entries:
        raise SystemExit("[x] 日报索引为空,无法生成")

    def entry_date(d):
        return d.get("date") or (d.get("report") or {}).get("date") or ""

    dates = sorted({entry_date(d) for d in entries if entry_date(d)})
    latest = dates[-1]
    print("[i] 使用最近一期:%s" % latest)
    return fetch("/dailies/%s" % latest)["report"], True


# ---------------------------------------------------------------- 分类

def guess_section(text):
    """
    加权打分把一条快讯归到某个版块。
    标题权重高于来源名;同分时按 SECTIONS 声明顺序取靠前者。
    """
    title = (text or "").lower()
    best_sid, best_score = "sec-industry", 0
    for sid, kws in SECTION_KEYWORDS.items():
        score = sum(w for kw, w in kws if kw in title)
        if score > best_score:
            best_sid, best_score = sid, score
    return best_sid


def norm_title(t):
    """标题归一化,用于去重比较"""
    return re.sub(r"[\s\W_]+", "", (t or "").lower())


def is_duplicate(title, seen):
    """
    同一事件被多家转载时视为重复。
    seen 为已收录条目的归一化标题列表。
    判定:完全相同,或公共前缀 >= 12 字且两条长度都 >= 20 字(长度差 >= 4,
    避免把同一起始的并列标题误合并)。
    """
    n = norm_title(title)
    if not n:
        return False
    for prev in seen:
        if not prev:
            continue
        if n == prev:
            return True
        m = min(len(n), len(prev))
        cp = 0
        while cp < m and n[cp] == prev[cp]:
            cp += 1
        if cp >= 12 and m >= 20 and abs(len(n) - len(prev)) >= 4:
            return True
    return False


def classify_flashes(flashes):
    """快讯无版块信息,按标题关键词归类,返回 {section_id: [item,...]}"""
    buckets = {}
    for f in flashes:
        sid = guess_section(f.get("title", ""))
        buckets.setdefault(sid, []).append({
            "title": f.get("title", ""),
            "summary": "",
            "source": (f.get("source") or {}).get("name", "AI HOT"),
            "url": (f.get("links") or {}).get("aihot") or (f.get("links") or {}).get("original") or "",
            "original": (f.get("links") or {}).get("original") or "",
            "publishedAt": f.get("publishedAt", ""),
        })
    return buckets


# ---------------------------------------------------------------- 工具

def to_beijing(iso):
    """ISO 时间 -> 北京时间对象;无值返回 None"""
    if not iso:
        return None
    s = iso.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=8)))


def human_time(iso, fallback_label="AI HOT 收录"):
    """转成北京时间人话,绝不返回 ISO 串"""
    dt = to_beijing(iso)
    if not dt:
        return fallback_label
    today = datetime.now(timezone(timedelta(hours=8))).date()
    day = "今天" if dt.date() == today else ("昨天" if dt.date() == today - timedelta(days=1) else "%d 月 %d 日" % (dt.month, dt.day))
    return "%s %02d:%02d" % (day, dt.hour, dt.minute)


WEEKDAY = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def clip(text, limit=SUMMARY_MAX):
    """中文摘要裁剪到 <= limit 字,尽量断在句号"""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return "该条目来自 AI HOT 收录,点击查看原文详情。"
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for sep in ("。", "；", "，", "、", " "):
        p = cut.rfind(sep)
        if p >= limit * 0.6:
            return cut[: p + 1] if sep != " " else cut[:p]
    return cut.rstrip("，、；") + "…"


def esc(s):
    return html.escape(s or "", quote=True)


# ---------------------------------------------------------------- 渲染

ICON = {
    "sec-model": '<path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>',
    "sec-product": '<path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z" fill="none" stroke="#fff" stroke-width="2"/>',
    "sec-industry": '<path d="M3 21h18M5 21V7l7-4 7 4v14M9 21v-6h6v6" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    "sec-paper": '<path d="M4 19.5A2.5 2.5 0 016.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    "sec-tips": '<path d="M9 18h6M10 22h4M12 2a7 7 0 00-4 12.7V17h8v-2.3A7 7 0 0012 2z" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
}

ARROW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg>'
DOT = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10"/></svg>'


def render_card(idx, item):
    url = item.get("url") or item.get("original") or CANONICAL
    title = esc(item["title"])
    src = esc(item.get("source") or "AI HOT")
    summary = esc(item.get("summary") or "")
    when = esc(item.get("timeLabel") or "")
    time_html = '<span class="card-time">%s</span>' % when if when else ""
    return """    <article class="card">
      <span class="card-num">%02d</span>
      <h3 class="card-title">%s</h3>
      <div class="card-meta">
        <span class="card-source">%s<span>%s</span></span>
        %s
      </div>
      <p class="card-summary">%s</p>
      <a class="card-link" href="%s" target="_blank" rel="noopener noreferrer" aria-label="阅读原文：%s">阅读原文 %s</a>
    </article>""" % (idx, title, DOT, src, time_html, summary, esc(url), title, ARROW)


def build_html(report, fell_back):
    data = report.get("date")
    dt = to_beijing(report.get("generatedAt")) or datetime.now(timezone(timedelta(hours=8)))
    date_human = "%d 年 %d 月 %d 日 · %s · 北京时间" % (dt.year, dt.month, dt.day, WEEKDAY[dt.weekday()])

    # 组装五个版块,全局连续编号
    buckets = {}
    total = 0
    seen_titles = []
    dropped = 0
    for sec in report.get("sections", []):
        sid = guess_section(sec.get("label", ""))
        # label 精确匹配优先
        for cand, label in SECTIONS:
            if label.replace(" ", "") == sec.get("label", "").replace(" ", ""):
                sid = cand
                break
        buckets.setdefault(sid, [])
        for it in sec.get("items", []):
            if is_duplicate(it.get("title", ""), seen_titles):
                dropped += 1
                continue
            seen_titles.append(norm_title(it.get("title", "")))
            total += 1
            buckets[sid].append({
                "title": it.get("title", ""),
                "summary": clip(it.get("summary", "")),
                "source": (it.get("source") or {}).get("name", "AI HOT"),
                "url": (it.get("links") or {}).get("aihot") or (it.get("links") or {}).get("original") or "",
                "timeLabel": "",
            })

    flash_buckets = classify_flashes(report.get("flashes", []))
    flash_count = 0
    for sid, items in flash_buckets.items():
        buckets.setdefault(sid, [])
        for it in items:
            if is_duplicate(it["title"], seen_titles):
                dropped += 1
                continue
            seen_titles.append(norm_title(it["title"]))
            flash_count += 1
            total += 1
            buckets[sid].append({
                "title": it["title"],
                "summary": clip(""),
                "source": it["source"],
                "url": it["url"] or it["original"],
                "timeLabel": human_time(it["publishedAt"]),
            })
    if dropped:
        print("[i] 已合并重复报道 %d 条" % dropped)

    # 正文条目取日报窗口区间作为时间说明
    ws, we = to_beijing(report.get("windowStart")), to_beijing(report.get("windowEnd"))
    window_human = ""
    if ws and we:
        window_human = "收录区间：%d 月 %d 日 %02d:00 — %d 月 %d 日 %02d:00（北京时间）" % (
            ws.month, ws.day, ws.hour, we.month, we.day, we.hour)

    # 各版块统计
    counts = [(sid, label, len(buckets.get(sid, []))) for sid, label in SECTIONS]
    sources = set()
    for sid in buckets:
        for it in buckets[sid]:
            sources.add(it["source"])

    lead = report.get("lead") or {}
    lead_html = ""
    if lead.get("title"):
        lead_html = """<div class="lead-wrap"><div class="lead">
  <span class="kicker">今日头条</span>
  <h3>%s</h3>
  <p>%s</p>
</div></div>""" % (esc(lead["title"]), esc(clip(lead.get("leadParagraph") or lead.get("summary") or "", 90)))

    # 正文
    sec_html = []
    n = 0
    for sid, label in SECTIONS:
        items = buckets.get(sid, [])
        if not items:
            continue
        cards = []
        for it in items:
            n += 1
            cards.append(render_card(n, it))
        sec_html.append("""<section class="section" id="%s">
<div class="section-inner">
  <div class="section-head">
    <span class="icon"><svg viewBox="0 0 24 24">%s</svg></span>
    <h2>%s</h2>
    <span class="count">%d 条</span>
  </div>
  <div class="cards">
%s
  </div>
</div>
</section>""" % (sid, ICON[sid], label, len(items), "\n\n".join(cards)))

    nav_html = "\n".join(
        '      <button type="button" data-target="%s">%s</button>' % (sid, label.replace(" ", "").replace("/更新", "").replace("/", ""))
        for sid, label in SECTIONS)

    stats_html = "\n".join(
        '      <div class="stat-card"><div class="stat-num" data-target="%d">0</div><div class="stat-label">%s</div></div>'
        % (v, lbl) for v, lbl in [
            (total, "今日条数"),
            (5, "分类版块"),
            (len(sources), "精选信源"),
            (total - flash_count, "深度条目"),
            (flash_count, "快讯更新"),
        ])

    fallback_badge = '<span class="latest-badge">最新一期</span>' if fell_back else ""

    return TEMPLATE % {
        "title": esc("AI 日报 · %s" % data),
        "date_short": esc(data[5:].replace("-", "-")),
        "fallback_badge": fallback_badge,
        "date_human": esc(date_human),
        "tagline": esc(lead.get("title") or "AI HOT 精选合辑"),
        "window_human": esc(window_human),
        "stats": stats_html,
        "lead_html": lead_html,
        "nav": nav_html,
        "sections": "\n\n".join(sec_html),
        "total": total,
        "date_human2": esc(date_human),
        "canonical": esc("%s/%s" % (CANONICAL, data)),
        "css": CSS,
        "js": JS,
    }


CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg-primary:#FFF9F2;--bg-card:#FFFFFF;--bg-elevated:#FFF4E6;
  --text-primary:#1A1A1A;--text-secondary:#5C5C5C;--text-muted:#8A8A8A;
  --accent:#FF6B35;--accent-hover:#E85525;--accent-light:#FFE4D6;
  --gradient:linear-gradient(135deg,#FF6B35 0%,#F7B801 100%);
  --gradient-soft:linear-gradient(135deg,#FFE4D6 0%,#FFF4D6 100%);
  --border:#F0E4D4;--border-hover:#FFB89A;
  --radius:14px;
  --shadow:0 4px 20px rgba(255,107,53,0.08);
  --shadow-hover:0 8px 28px rgba(255,107,53,0.16);
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue","PingFang SC","Microsoft YaHei",sans-serif;
  --font-mono:"SF Mono","Fira Code",Menlo,Consolas,monospace;
  --transition:0.25s cubic-bezier(0.4,0,0.2,1);
}
html{scroll-behavior:smooth}
body{font-family:var(--font);background:var(--bg-primary);color:var(--text-primary);line-height:1.6;overflow-x:hidden;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
.topnav{position:sticky;top:0;z-index:50;background:rgba(255,249,242,0.92);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-bottom:1px solid var(--border)}
.topnav-inner{max-width:1100px;margin:0 auto;padding:14px 24px;display:flex;align-items:center;gap:24px;overflow-x:auto}
.topnav-brand{font-weight:700;font-size:0.95rem;display:flex;align-items:center;gap:8px;white-space:nowrap}
.topnav-brand .dot{width:10px;height:10px;border-radius:50%;background:var(--gradient);box-shadow:0 0 0 3px var(--accent-light);animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 3px var(--accent-light)}50%{box-shadow:0 0 0 6px rgba(255,107,53,0.15)}}
.topnav-links{display:flex;gap:6px;flex:1}
.topnav-links button{appearance:none;border:0;background:transparent;font:inherit;cursor:pointer;font-size:0.85rem;color:var(--text-secondary);padding:6px 12px;border-radius:999px;white-space:nowrap;transition:var(--transition)}
.topnav-links button:hover,.topnav-links button.active{background:var(--accent-light);color:var(--accent)}
.hero{padding:80px 24px 60px;text-align:center;background:var(--gradient-soft);position:relative;overflow:hidden}
.hero::before{content:"";position:absolute;top:-100px;right:-100px;width:300px;height:300px;border-radius:50%;background:radial-gradient(circle,rgba(255,107,53,0.15) 0%,transparent 70%)}
.hero::after{content:"";position:absolute;bottom:-80px;left:-80px;width:240px;height:240px;border-radius:50%;background:radial-gradient(circle,rgba(247,184,1,0.18) 0%,transparent 70%)}
.hero-inner{max-width:900px;margin:0 auto;position:relative;z-index:1}
.date-badge{display:inline-flex;align-items:center;gap:8px;padding:6px 16px;border-radius:999px;background:var(--bg-card);border:1px solid var(--border);font-size:0.85rem;color:var(--text-secondary);margin-bottom:20px;box-shadow:var(--shadow)}
.date-badge .live{width:8px;height:8px;border-radius:50%;background:#22C55E;animation:live-pulse 1.6s ease-out infinite}
@keyframes live-pulse{0%{box-shadow:0 0 0 0 rgba(34,197,94,0.5)}100%{box-shadow:0 0 0 10px rgba(34,197,94,0)}}
.latest-badge{background:var(--accent-light);color:var(--accent);font-weight:600;padding:1px 8px;border-radius:999px}
.hero h1{font-size:clamp(2.2rem,5.5vw,3.6rem);font-weight:800;letter-spacing:-1px;line-height:1.15;margin-bottom:14px}
.hero h1 .accent{background:var(--gradient);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.hero-tagline{font-size:clamp(1rem,1.6vw,1.15rem);color:var(--text-secondary);margin-bottom:12px}
.hero-window{font-size:0.82rem;color:var(--text-muted);margin-bottom:36px}
.hero-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;max-width:900px;margin:0 auto}
.stat-card{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:18px 14px;text-align:center;box-shadow:var(--shadow);transition:var(--transition)}
.stat-card:hover{transform:translateY(-3px);box-shadow:var(--shadow-hover);border-color:var(--border-hover)}
.stat-num{font-size:2.2rem;font-weight:800;font-family:var(--font-mono);background:var(--gradient);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;line-height:1}
.stat-label{font-size:0.78rem;color:var(--text-muted);margin-top:6px;letter-spacing:0.5px}
.lead-wrap{max-width:1100px;margin:0 auto;padding:34px 24px 0}
.lead{background:var(--bg-card);border:1px solid var(--border);border-left:4px solid var(--accent);border-radius:var(--radius);padding:20px 24px;box-shadow:var(--shadow)}
.lead .kicker{display:inline-block;font-size:0.72rem;font-weight:700;letter-spacing:1px;color:var(--accent);background:var(--accent-light);padding:3px 10px;border-radius:999px;margin-bottom:10px}
.lead h3{font-size:1.15rem;font-weight:700;line-height:1.5;margin-bottom:8px;letter-spacing:-0.2px}
.lead p{font-size:0.92rem;color:var(--text-secondary)}
.section{padding:52px 24px 20px;scroll-margin-top:64px}
.section-inner{max-width:1100px;margin:0 auto}
.section-head{display:flex;align-items:center;gap:14px;margin-bottom:26px;padding-bottom:14px;border-bottom:2px dashed var(--border)}
.section-head .icon{width:34px;height:34px;border-radius:10px;background:var(--gradient);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.section-head .icon svg{width:18px;height:18px;fill:#fff}
.section-head h2{font-size:1.5rem;font-weight:700;letter-spacing:-0.3px}
.section-head .count{font-size:0.8rem;color:var(--accent);background:var(--accent-light);font-family:var(--font-mono);padding:3px 10px;border-radius:999px;margin-left:auto;align-self:center}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.card{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:20px 22px;display:flex;flex-direction:column;position:relative;overflow:hidden;transition:var(--transition)}
html.js-ready .card{opacity:0;transform:translateY(16px)}
html.js-ready .card.visible,.card.visible{opacity:1;transform:none}
.card::before{content:"";position:absolute;top:0;left:0;right:0;height:3px;background:var(--gradient);transform:scaleX(0);transform-origin:left;transition:transform 0.3s ease}
.card:hover{border-color:var(--border-hover);box-shadow:var(--shadow-hover);transform:translateY(-3px)}
.card:hover::before{transform:scaleX(1)}
.card-num{position:absolute;top:14px;right:18px;font-family:var(--font-mono);font-size:0.78rem;color:var(--text-muted);background:var(--bg-elevated);padding:2px 8px;border-radius:6px}
.card-title{font-size:1.02rem;font-weight:600;line-height:1.45;margin-bottom:10px;padding-right:48px;letter-spacing:-0.2px}
.card-meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:10px}
.card-source{display:inline-flex;align-items:center;gap:6px;font-size:0.75rem;color:var(--text-muted);padding:3px 10px;background:var(--bg-elevated);border-radius:999px;max-width:100%}
.card-source svg{width:11px;height:11px;flex-shrink:0;opacity:0.6}
.card-source span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-time{display:inline-flex;align-items:center;gap:5px;font-size:0.72rem;color:var(--text-muted);padding:3px 9px;border:1px solid var(--border);border-radius:999px;font-family:var(--font-mono);white-space:nowrap}
.card-summary{font-size:0.88rem;color:var(--text-secondary);line-height:1.6;margin-bottom:14px;flex:1}
.card-link{display:inline-flex;align-items:center;gap:6px;font-size:0.82rem;font-weight:500;color:var(--accent);margin-top:auto;align-self:flex-start;transition:var(--transition)}
.card-link svg{width:14px;height:14px;transition:transform 0.2s ease}
.card-link:hover{color:var(--accent-hover)}
.card-link:hover svg{transform:translate(2px,-2px)}
footer{margin-top:60px;padding:40px 24px;text-align:center;border-top:1px solid var(--border);background:var(--bg-elevated)}
.footer-stats{display:inline-flex;align-items:center;gap:14px;flex-wrap:wrap;justify-content:center;font-size:0.85rem;color:var(--text-secondary)}
.footer-stats .sep{color:var(--text-muted)}
.footer-source{font-size:0.78rem;color:var(--text-muted);margin-top:8px}
.footer-source a{color:var(--accent);font-weight:500}
.footer-note{font-size:0.74rem;color:var(--text-muted);margin-top:6px}
@media (max-width:768px){
  .hero{padding:60px 20px 50px}
  .lead-wrap{padding:24px 16px 0}
  .section{padding:42px 16px 12px}
  .cards{grid-template-columns:1fr;gap:14px}
  .card{padding:18px}
  .topnav-inner{padding:12px 16px;gap:14px}
  .section-head{flex-wrap:wrap;gap:10px}
  .section-head .count{margin-left:0}
}
"""

JS = """
(function(){
var supportsObserver='IntersectionObserver' in window;
var sections=document.querySelectorAll('section[id]');
var navLinks=document.querySelectorAll('.topnav-links button');
function setActiveNav(id){navLinks.forEach(function(b){b.classList.toggle('active',b.dataset.target===id);});}
navLinks.forEach(function(b){
  b.addEventListener('click',function(){
    var s=document.getElementById(b.dataset.target);
    if(!s)return;
    setActiveNav(s.id);
    try{s.scrollIntoView({behavior:'smooth',block:'start'});}catch(_){s.scrollIntoView(true);}
  });
});
function animateNum(el,target,duration){
  var start=performance.now();
  function tick(now){
    var t=Math.min((now-start)/duration,1);
    el.textContent=Math.round((1-Math.pow(1-t,3))*target);
    if(t<1)requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}
var statNums=document.querySelectorAll('.stat-num[data-target]');
if(supportsObserver){
  var numObserver=new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if(e.isIntersecting){
        var target=parseInt(e.target.dataset.target,10);
        if(!isNaN(target))animateNum(e.target,target,1200);
        numObserver.unobserve(e.target);
      }
    });
  },{threshold:0.4});
  statNums.forEach(function(el){numObserver.observe(el);});
}else{
  statNums.forEach(function(el){el.textContent=el.dataset.target;});
}
var cards=document.querySelectorAll('.card');
if(supportsObserver){
  var cardObserver=new IntersectionObserver(function(entries){
    entries.forEach(function(e,i){
      if(e.isIntersecting){
        e.target.style.transitionDelay=(i%6)*40+'ms';
        e.target.classList.add('visible');
        cardObserver.unobserve(e.target);
      }
    });
  },{threshold:0.15});
  cards.forEach(function(el){cardObserver.observe(el);});
}else{
  cards.forEach(function(el){el.classList.add('visible');});
}
if(supportsObserver){
  var spy=new IntersectionObserver(function(entries){
    entries.forEach(function(e){if(e.isIntersecting)setActiveNav(e.target.id);});
  },{rootMargin:'-40% 0px -55% 0px'});
  sections.forEach(function(s){spy.observe(s);});
}
})();
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="generator" content="aihot-site">
<title>%(title)s</title>
<script>document.documentElement.classList.add('js-ready');</script>
<style>%(css)s</style>
</head>
<body>

<nav class="topnav" aria-label="版块导航">
  <div class="topnav-inner">
    <div class="topnav-brand"><span class="dot"></span>AI 日报 · %(date_short)s</div>
    <div class="topnav-links">
%(nav)s
    </div>
  </div>
</nav>

<header class="hero">
  <div class="hero-inner">
    <div class="date-badge"><span class="live"></span>%(date_human)s %(fallback_badge)s</div>
    <h1>今日 <span class="accent">AI</span> 圈发生了什么</h1>
    <p class="hero-tagline">%(tagline)s</p>
    <p class="hero-window">%(window_human)s</p>
    <div class="hero-stats">
%(stats)s
    </div>
  </div>
</header>

%(lead_html)s

<main>
%(sections)s
</main>

<footer>
  <div class="footer-stats">
    <span>共 <strong>%(total)d</strong> 条</span>
    <span class="sep">·</span>
    <span>5 大版块</span>
    <span class="sep">·</span>
    <span>%(date_human2)s</span>
  </div>
  <div class="footer-source">数据来自 <a href="%(canonical)s" target="_blank" rel="noopener noreferrer">AI HOT 日报</a></div>
  <div class="footer-note">时间均为北京时间；第三方原文版权归原作者所有</div>
</footer>

<script>%(js)s</script>
</body>
</html>
"""


# ---------------------------------------------------------------- 归档索引

INDEX_TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 日报归档</title>
<style>%(css)s
body{padding:0}
.arch{max-width:820px;margin:0 auto;padding:70px 24px 40px;text-align:center}
.arch h1{font-size:clamp(1.9rem,5vw,2.8rem);font-weight:800;letter-spacing:-1px;margin-bottom:10px}
.arch p.sub{color:var(--text-secondary);margin-bottom:34px}
.arch-list{display:grid;gap:12px;text-align:left}
.arch-item{display:flex;align-items:center;justify-content:space-between;gap:14px;background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:16px 20px;box-shadow:var(--shadow);transition:var(--transition)}
.arch-item:hover{border-color:var(--border-hover);box-shadow:var(--shadow-hover);transform:translateY(-2px)}
.arch-left{display:flex;flex-direction:column;gap:2px;min-width:0}
.arch-date{font-family:var(--font-mono);font-size:1rem;font-weight:700}
.arch-meta{font-size:0.78rem;color:var(--text-muted)}
.arch-go{font-size:0.82rem;font-weight:500;color:var(--accent);display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.empty{padding:40px 0;color:var(--text-muted);font-size:0.9rem}
@media (max-width:768px){.arch{padding:48px 16px 30px}}
</style>
</head>
<body>
<div class="arch">
  <h1>AI 日报归档</h1>
  <p class="sub">共 %(count)d 期 · 最新一期 %(latest)s</p>
  <div class="arch-list">
%(items)s
  </div>
</div>
</body>
</html>
"""

ARCH_ITEM = """    <a class="arch-item" href="%(href)s">
      <span class="arch-left">
        <span class="arch-date">%(date)s</span>
        <span class="arch-meta">%(weekday)s · %(count)s</span>
      </span>
      <span class="arch-go">查看 %(arrow)s</span>
    </a>"""


def build_index(outdir):
    """扫描 output 目录里的日报 HTML,生成归档索引 index.html"""
    files = []
    for name in os.listdir(outdir):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\.html$", name)
        if m:
            files.append(m.group(1))
    files.sort(reverse=True)

    items = []
    items_dates = []
    for d in files:
        path = os.path.join(outdir, "%s.html" % d)
        try:
            content = open(path, encoding="utf-8").read()
            cm = re.search(r"<strong>(\d+)</strong>\s*条", content)
            count = int(cm.group(1)) if cm else 0
        except OSError:
            continue
        # 源数据本身为空的期次(AI HOT 未收录任何条目)不入归档,避免误导性的"0 条"
        if count <= 0:
            continue
        try:
            y, m, dd = (int(x) for x in d.split("-"))
            wd = WEEKDAY[datetime(y, m, dd).weekday()]
        except ValueError:
            wd = ""
        items.append(ARCH_ITEM % {
            "href": "%s.html" % d,
            "date": d,
            "weekday": wd,
            "count": count,
            "arrow": ARROW,
        })
        items_dates.append(d)

    body = "\n".join(items) or '<p class="empty">还没有生成任何日报。</p>'
    # 期数按实际收录条目数统计(items 已排除源数据为空的期次)
    latest = items_dates[0] if items_dates else "—"
    return INDEX_TPL % {"css": CSS, "count": len(items), "latest": latest, "items": body}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="AI HOT 日报 → HTML 晨报仪表盘")
    ap.add_argument("--date", help="指定日期 YYYY-MM-DD,默认今天")
    ap.add_argument("--out", default="output", help="输出目录,默认 output")
    args = ap.parse_args()

    report, fell_back = fetch_daily(args.date)
    date_str = report["date"]
    page = build_html(report, fell_back)

    outdir = os.path.abspath(args.out)
    os.makedirs(outdir, exist_ok=True)

    page_path = os.path.join(outdir, "%s.html" % date_str)
    with open(page_path, "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index(outdir))

    total = int(re.search(r"<strong>(\d+)</strong>", page).group(1))
    print("[✓] %s  共 %d 条" % (page_path, total))
    print("[✓] %s" % os.path.join(outdir, "index.html"))


if __name__ == "__main__":
    main()
