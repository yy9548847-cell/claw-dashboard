#!/usr/bin/env python3
"""云端内容生成：数据(data/data.json) + LLM → 新卡片 HTML → splice 进 index.html

LLM 双通道：优先 GitHub Models（GITHUB_TOKEN，免费），失败回退 DeepSeek（DEEPSEEK_API_KEY）。
DRY_RUN=1 时只生成不提交。至少一个模块成功才算成功。
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

CN = timezone(timedelta(hours=8))
NOW = datetime.now(CN)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
INDEX = os.path.join(ROOT, "index.html")
DATA = json.load(open(os.path.join(ROOT, "data", "data.json"), encoding="utf-8"))
DRY = os.environ.get("DRY_RUN", "") == "1"
MODEL = os.environ.get("LLM_MODEL", "openai/gpt-4o-mini")

WEEKDAY = "一二三四五六日"[NOW.weekday()]
TODAY_CN = f"{NOW.month}月{NOW.day}日"

SYSTEM = """你是中文信息编辑，为大众向资讯产品「Claw 资讯台」撰写每日页面。铁律：
1. 只输出 HTML 片段本身（不要 markdown 代码块、不要解释、不要 <div class="page"> 外壳）。
2. 可用元素： <div class="section-title">…</div>（页首小标题，必须包含今日日期）；卡片外壳 <div class="card">…</div>；卡内可用 <span class="tag">标签</span>（强调可用 class="tag gold" 或 class="tag hot"）、<h3>标题</h3>、<p class="note">…</p>、<ul><li>…</li></ul>、行情报价块 <div class="quote"><div class="name">名称<small>代码</small></div><div class="px"><b class="up">点位</b><span class="up">+x.xx%</span></div></div>（涨用 up 跌用 down）、点评块 <div class="ai"><b>🎯 标签：</b>…</div>。
3. 【来源铁律】每张卡片末尾必须有 <a class="src" href="URL">🔗 来源名 · 日期</a>，URL 只能用输入数据里给出的真实链接，一个都不许编造、不许改写；数据里没有链接的主题不要写。
4. 排版：单个 <p> 不超过 200 字，长内容拆 <ul><li>；数字用 <b>加粗；面向零基础普通读者，禁程序员黑话。
5. 事实必须来自输入数据，禁止编造数字和事件。"""


def page_inner(html, pid):
    """提取现有页面内容作为风格样例（同 splice 的深度配平逻辑）"""
    start = html.find(f'<div class="page" id="{pid}">')
    if start < 0:
        return ""
    body_start = start + len(f'<div class="page" id="{pid}">')
    depth, i = 1, body_start
    while i < len(html):
        o = re.compile(r"<div\b", re.I).search(html, i)
        c = re.compile(r"</div>", re.I).search(html, i)
        if not c:
            return ""
        if o and o.start() < c.start():
            depth += 1
            i = o.end()
        else:
            depth -= 1
            if depth == 0:
                return html[body_start:c.start()].strip()
            i = c.end()
    return ""


def splice(html, pid, inner):
    start = html.find(f'<div class="page" id="{pid}">')
    if start < 0:
        raise RuntimeError(f"页面不存在 {pid}")
    body_start = start + len(f'<div class="page" id="{pid}">')
    depth, i = 1, body_start
    while i < len(html):
        o = re.compile(r"<div\b", re.I).search(html, i)
        c = re.compile(r"</div>", re.I).search(html, i)
        if not c:
            raise RuntimeError(f"div 未闭合 {pid}")
        if o and o.start() < c.start():
            depth += 1
            i = o.end()
        else:
            depth -= 1
            if depth == 0:
                return html[:body_start] + "\n" + inner + "\n  " + html[c.start():]
            i = c.end()
    raise RuntimeError(f"未找到闭合 {pid}")


def llm(user_prompt):
    """GitHub Models → DeepSeek 回退"""
    body = json.dumps({"model": MODEL, "messages": [
        {"role": "system", "content": SYSTEM}, {"role": "user", "content": user_prompt}],
        "temperature": 0.4, "max_tokens": 4000}).encode()
    attempts = []
    gt = os.environ.get("GITHUB_TOKEN", "")
    if gt:
        attempts.append(("github-models",
                         "https://models.github.ai/inference/chat/completions",
                         {"Authorization": f"Bearer {gt}"}, MODEL, None))
    dk = os.environ.get("DEEPSEEK_API_KEY", "")
    if dk:
        attempts.append(("deepseek", "https://api.deepseek.com/chat/completions",
                         {"Authorization": f"Bearer {dk}"}, "deepseek-chat", None))
    for name, url, hdr, model, _ in attempts:
        try:
            payload = body
            if name == "deepseek":
                payload = json.loads(body)
                payload["model"] = model
                payload = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=payload, method="POST",
                                         headers={**hdr, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.loads(r.read())
            text = resp["choices"][0]["message"]["content"].strip()
            text = re.sub(r"^```(?:html)?\s*|\s*```$", "", text)
            if len(text) > 200:
                print(f"LLM OK via {name} ({len(text)} chars)")
                return text
            print(f"WARN {name} 返回过短: {text[:120]}")
        except Exception as e:
            print(f"WARN {name} 失败: {e}")
    return None


def valid(frag, pid):
    if not frag:
        return False
    if "<div class=\"card\"" not in frag and pid != "p-60s":
        return False
    if "class=\"srow\"" not in frag and pid == "p-60s":
        return False
    if pid != "p-60s" and "class=\"src\"" not in frag:
        return False
    if "<div class=\"page\"" in frag:
        return False
    return frag.count("<div") == frag.count("</div>")


def us_title(us):
    if not us:
        return ""
    d0 = us[0]["date"]
    _, m, d = d0.split("-")
    return f"隔夜美股（{int(m)}/{int(d)} 收盘）"


def build_mkt():
    idx = DATA["indices"]
    a, us = idx.get("a", []), idx.get("us", [])
    if not a:
        return None, "行情数据缺失"
    a0 = a[0]["date"].split("-")
    sec = f"A股收盘 {int(a0[1])}月{int(a0[2])}日（{WEEKDAY}）· {us_title(us)}"
    return llm(f"""任务：重写「行情」页面（页面 id p-mkt）。只输出页面内容片段。
section-title 用：{sec}
今日数据 JSON：
{json.dumps(idx, ensure_ascii=False)}
要求：
1. 卡1：A股三大指数，用 quote 块展示点位与涨跌幅（涨 up 跌 down），加一段 note 概括盘面（谁领涨/量能），来源链接用下方提供。
2. 卡2：美股三大指数 quote 块 + 简评。
3. 卡3：市场主线/看点 1 张（基于数据推断，点评放 .ai 块；此卡若无可靠素材来源可省略）。
4. 全部数字必须来自 JSON，JSON 没有的不要写。可选来源链接（只能用这些）：
- 东方财富行情页 https://quote.eastmoney.com/zs000001.html
- 东方财富美股 https://quote.eastmoney.com/center/gridlist.html
"""), "p-mkt"


def build_git():
    tr = DATA.get("trending", [])
    if len(tr) < 4:
        return None, "trending 数据不足"
    return llm(f"""任务：重写「GitHub 趋势」页面（页面 id p-git）。只输出页面内容片段。
section-title 用：GitHub 趋势 · {TODAY_CN}（{WEEKDAY}）快照
数据（选 5-8 个，优先对普通人有用/有意思的，star 数照抄）：
{json.dumps(tr[:12], ensure_ascii=False)}
要求：每项目一张卡：tag（类别，如 🤖 AI 工具 / 📦 效率）+ h3（项目名 + 一句中文卖点）+ p（它是什么、解决什么问题，人话）+ a.src 链接（url 字段）。
最后加一张趋势观察卡（tag gold「趋势观察」），归纳今天榜面的共同主题，点评放 .ai 块。
"""), "p-git"


def build_paper():
    pp = DATA.get("papers", [])
    if len(pp) < 3:
        return None, "论文数据不足"
    return llm(f"""任务：重写「论文速递」页面（页面 id p-paper）。只输出页面内容片段。
section-title 用：arXiv 精选 · 数据截至 {TODAY_CN} · 近两日 cs.AI/CL/LG 新稿
数据（挑 2-4 篇最值得普通人了解的）：
{json.dumps(pp, ensure_ascii=False)}
要求：每篇一张卡：tag（主题）+ h3（起个中文小标题，别直译）+ p「<b>一句话：</b>人话概括这篇在干什么」+ p 或 ul 补关键发现（数字照抄 summary）+ div.ai「<b>🔍 为什么值得看：</b>…」+ a.src（url 字段，格式 🔗 arXiv:编号）。
"""), "p-paper"


def build_ai():
    tt = DATA.get("toutiao", [])
    hn = DATA.get("hn", [])
    if len(tt) < 5:
        return None, "头条数据不足"
    return llm(f"""任务：重写「AI 日报」页面（页面 id p-ai）。只输出页面内容片段，分两个区域。
【区域一】section-title 用：AI 日报 · {TODAY_CN}（{WEEKDAY}）· 国内要闻
从下方头条热榜里筛出 AI/科技相关条目写 4-6 张卡（不够 AI 就选最重要的科技/社会要闻，tag 标注真实主题，不要硬说成 AI）：
{json.dumps(tt[:30], ensure_ascii=False)}
【区域二】页尾加 section-title：🌍 海外一手源 · Hacker News 编译
从 HN 数据里挑 2-4 条写卡（tag 用 🌍）：
{json.dumps(hn, ensure_ascii=False)}
要求：每卡末尾 a.src 用对应 url（热榜用条目 url，HN 用 hn 讨论链接或原文 url）；每卡配 div.ai「<b>💡 为什么值得看：</b>…」一句判断。
"""), "p-ai"


def build_60s(dones):
    brief = [{k: c[k] for k in ("page", "titles")} for c in dones]
    return llm(f"""任务：重写「今日60秒」页面（页面 id p-60s）——编辑部视角的 1 分钟导读。只输出页面内容片段。
格式：section-title 用「{TODAY_CN} · 预计阅读 1 分钟」，然后一个 <div class="card"> 包 4-5 行导读，每行结构（严格照抄）：
<div class="srow" data-go="页面id"><b>序号</b><div class="stx"><p>一句话事实（≤40字）</p><span>为什么值得看：一句判断</span><em>→ 目标模块中文名</em></div><i>›</i></div>
今日已生成的模块内容摘要（事实只能从中选，不得编造）：
{json.dumps(brief, ensure_ascii=False)}
data-go 可选值：p-ai、p-mkt、p-git、p-paper。覆盖不同模块，宁缺毋滥。
"""), "p-60s"


# 页面 id → data-go 可选页面（供 60s 校验）
def main():
    html = open(INDEX, encoding="utf-8").read()
    old_sub = re.search(r'<p id="hdSub">(\d{4}年\d+月\d+日 周[一二三四五六日])<br>', html)
    print("当前线上日期:", old_sub.group(1) if old_sub else "?")

    builders = [("p-mkt", build_mkt), ("p-git", build_git), ("p-paper", build_paper), ("p-ai", build_ai)]
    frags, dones, fails = {}, [], []
    for pid, fn in builders:
        try:
            frag, why = fn()
        except Exception as e:
            frag, why = None, str(e)
        if frag and valid(frag, pid):
            frags[pid] = frag
            dones.append({"page": pid, "titles": re.findall(r"<h3>([^<]+)</h3>", frag)})
            print(f"OK {pid}")
        else:
            fails.append((pid, why))
            print(f"SKIP {pid}: {why}")

    if not dones:
        print("所有模块失败，不发布")
        sys.exit(1)

    try:
        frag, why = build_60s(dones)
        if frag and valid(frag, "p-60s"):
            frags["p-60s"] = frag
            print("OK p-60s")
        else:
            fails.append(("p-60s", why))
            print(f"SKIP p-60s: {why}")
    except Exception as e:
        print(f"SKIP p-60s: {e}")

    for pid, frag in frags.items():
        html = splice(html, pid, frag)

    # 页头日期
    new_sub = f'{NOW.year}年{NOW.month}月{NOW.day}日 周{WEEKDAY}'
    if old_sub:
        html = html.replace(f'<p id="hdSub">{old_sub.group(1)}<br>',
                            f'<p id="hdSub">{new_sub}<br>', 1)
        if f'<p id="hdSub">{new_sub}<br>' not in html:
            raise RuntimeError("页头日期替换失败")

    with open(INDEX, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已更新 {len(frags)} 页: {', '.join(frags)}；失败: {[f[0] for f in fails]}")

    # 基本自检
    assert html.count("<div") == html.count("</div>"), "div 不配平"
    assert html.count('id="backBtn"') == 1, "backBtn 异常"
    for u in re.findall(r'class="src" href="([^"]+)"', html):
        assert u.startswith("http"), f"非法链接 {u}"

    if DRY:
        open(os.path.join(ROOT, "data", "index.dryrun.html"), "w", encoding="utf-8").write(html)
        print("DRY_RUN：结果另存 data/index.dryrun.html，不提交")
        return
    env = {**os.environ, "GIT_AUTHOR_NAME": "claw-bot", "GIT_AUTHOR_EMAIL": "claw-bot@users.noreply.github.com",
           "GIT_COMMITTER_NAME": "claw-bot", "GIT_COMMITTER_EMAIL": "claw-bot@users.noreply.github.com"}
    subprocess.run(["git", "add", "index.html"], cwd=ROOT, check=True, env=env)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, env=env).returncode
    if diff == 0:
        print("内容无变化，跳过提交")
        return
    subprocess.run(["git", "commit", "-m", f"daily: auto update {DATA['today']}"], cwd=ROOT, check=True, env=env)
    subprocess.run(["git", "push"], cwd=ROOT, check=True, env=env)
    print("已提交并推送")


if __name__ == "__main__":
    main()
