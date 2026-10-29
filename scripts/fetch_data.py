#!/usr/bin/env python3
"""每日数据抓取（全部免费源、无需 API Key）→ data/data.json

数据源：
1. A股/美股指数日线：东方财富 push2his kline API
2. arXiv cs.AI/CL/LG 最新论文（近2天）
3. GitHub Trending（网页解析，失败则跳过）
4. 今日头条热榜（60s API）→ AI日报素材
5. Hacker News Top（ firebase API）→ 海外一手源素材
"""
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

CN = timezone(timedelta(hours=8))
NOW = datetime.now(CN)
TODAY = NOW.strftime("%Y-%m-%d")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
CTX = ssl.create_default_context()
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def get(url, timeout=25, headers=None, binary=False):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        raw = r.read()
        return raw if binary else raw.decode("utf-8", errors="replace")


# ---------- 1. 指数行情 ----------
def em_kline(secid, name):
    """取最近一个【已完成交易日】的收盘数据（今天未收盘的行会被剔除）"""
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=" + secid +
           "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt=10")
    rows = json.loads(get(url))["data"]["klines"]
    hist = []
    for r in rows:
        d, o, c, h, l, vol, amt = r.split(",")
        hist.append({"date": d, "close": float(c), "amount": float(amt)})
    done = [x for x in hist if x["date"] < TODAY]  # 今天的行在收盘前视为未完成
    if not done:
        return None
    last, prev = done[-1], (done[-2] if len(done) > 1 else None)
    pct = round((last["close"] - prev["close"]) / prev["close"] * 100, 2) if prev else None
    chg = round(last["close"] - prev["close"], 2) if prev else None
    return {"name": name, "date": last["date"], "close": last["close"],
            "chg": chg, "pct": pct, "amount_yi": round(last["amount"] / 1e8, 0)}


def fetch_indices():
    out = {"a": [], "us": []}
    for sid, nm in [("1.000001", "上证指数"), ("0.399001", "深证成指"), ("0.399006", "创业板指")]:
        try:
            k = em_kline(sid, nm)
            if k:
                out["a"].append(k)
        except Exception as e:
            print(f"WARN 行情 {nm}: {e}")
    for sid, nm in [("100.DJIA", "道琼斯"), ("100.NDX", "纳斯达克"), ("100.SPX", "标普500")]:
        try:
            k = em_kline(sid, nm)
            if k:
                out["us"].append(k)
        except Exception as e:
            print(f"WARN 美股 {nm}: {e}")
    return out


# ---------- 2. arXiv ----------
def fetch_arxiv():
    q = "https://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(
        "cat:cs.AI OR cat:cs.CL OR cat:cs.LG") + "&sortBy=submittedDate&sortOrder=descending&max_results=30"
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(get(q, timeout=40))
    cutoff = NOW - timedelta(days=2)
    items = []
    for e in root.findall("a:entry", ns):
        pub = datetime.fromisoformat(e.find("a:published", ns).text).replace(tzinfo=timezone.utc)
        if pub < cutoff:
            continue
        url = e.find("a:id", ns).text.strip()
        m = re.search(r"abs/([\w.]+)", url)
        items.append({
            "id": m.group(1) if m else "",
            "title": re.sub(r"\s+", " ", e.find("a:title", ns).text).strip(),
            "summary": re.sub(r"\s+", " ", e.find("a:summary", ns).text).strip()[:600],
            "url": url, "published": pub.astimezone(CN).strftime("%m-%d %H:%M"),
        })
        if len(items) >= 10:
            break
    return items


# ---------- 3. GitHub Trending ----------
def fetch_trending():
    try:
        html = get("https://github.com/trending", timeout=30)
    except Exception as e:
        print(f"WARN trending 不可达: {e}")
        return []
    arts = html.split("<article")[1:]
    out = []
    for a in arts[:15]:
        mh = re.search(r'href="/([\w.-]+/[\w.-]+)"', a)
        if not mh:
            continue
        repo = mh.group(1)
        md = re.search(r'<p class="[^"]*col-9[^"]*">\s*([^<]+?)\s*</p>', a)
        ms = re.search(r'href="/' + repo + r'/stargazers"[^>]*>\s*([\d,]+)\s*<', a)
        mt = re.search(r'([\d,]+)\s*stars today', a)
        ml = re.search(r'itemprop="programmingLanguage">([^<]+)<', a)
        out.append({
            "repo": repo, "desc": md.group(1).strip() if md else "",
            "stars": ms.group(1) if ms else "", "stars_today": mt.group(1) if mt else "",
            "lang": ml.group(1) if ml else "", "url": f"https://github.com/{repo}",
        })
    return out


# ---------- 4. 头条热榜 ----------
def fetch_toutiao():
    try:
        d = json.loads(get("https://60s.viki.moe/v2/toutiao", timeout=25))
        return [{"title": x.get("title", ""), "url": x.get("link") or x.get("url", ""),
                 "hot": x.get("hot_value", 0)} for x in d.get("data", [])[:35] if x.get("link") or x.get("url")]
    except Exception as e:
        print(f"WARN 头条热榜: {e}")
        return []


# ---------- 5. Hacker News ----------
def fetch_hn():
    try:
        ids = json.loads(get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=25))[:15]
        out = []
        for i in ids:
            try:
                it = json.loads(get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=15))
                if it.get("url"):
                    out.append({"title": it.get("title", ""), "url": it["url"],
                                "score": it.get("score", 0),
                                "comments": it.get("descendants", 0),
                                "hn": f"https://news.ycombinator.com/item?id={i}"})
            except Exception:
                continue
            if len(out) >= 10:
                break
        return out
    except Exception as e:
        print(f"WARN HN: {e}")
        return []


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    data = {
        "generated_at": NOW.strftime("%Y-%m-%d %H:%M %Z"),
        "today": TODAY,
        "weekday_cn": "周" + "一二三四五六日"[NOW.weekday()],
        "indices": fetch_indices(),
        "papers": fetch_arxiv(),
        "trending": fetch_trending(),
        "toutiao": fetch_toutiao(),
        "hn": fetch_hn(),
    }
    path = os.path.join(OUT_DIR, "data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("数据摘要: indices a=%d us=%d | papers=%d | trending=%d | toutiao=%d | hn=%d" % (
        len(data["indices"]["a"]), len(data["indices"]["us"]),
        len(data["papers"]), len(data["trending"]), len(data["toutiao"]), len(data["hn"])))
