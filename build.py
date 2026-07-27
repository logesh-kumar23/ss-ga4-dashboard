# -*- coding: utf-8 -*-
"""
Deterministic dashboard builder for the daily refresh task.

Reads raw Porter Metrics query dumps from ./raw/, transforms them into the
dashboard's data contract, and injects the result into index.template.html to
produce index.html.

The morning Cowork task must save these 7 raw Porter responses (verbatim JSON
as returned by the Porter query_data tool) into ./raw/ :

  raw/daily_channel.json   dims: date, sessionDefaultChannelGroup            (~90 days)
  raw/pagechan_cur.json    dims: pagePath, sessionDefaultChannelGroup        (current 28d)
  raw/pagechan_prev.json   dims: pagePath, sessionDefaultChannelGroup        (previous 28d)
  raw/device_cur.json      dims: deviceCategory                              (current 28d)
  raw/device_prev.json     dims: deviceCategory                              (previous 28d)
  raw/keyevent_cur.json    dims: eventName  (metricFilter keyEvents>0)       (current 28d)
  raw/keyevent_prev.json   dims: eventName  (metricFilter keyEvents>0)       (previous 28d)

channelCur / channelPrev (28d source totals) are DERIVED here from daily_channel,
so no separate channel query is needed.

Usage:  python build.py            # reads ./raw, writes ./index.html
"""
import json, os, sys, datetime

RAW = os.path.join(os.path.dirname(__file__), "raw")
TPL = os.path.join(os.path.dirname(__file__), "index.template.html")
OUT = os.path.join(os.path.dirname(__file__), "index.html")

MET = {  # column id -> field
    "google_analytics_4_sessions": "sessions",
    "google_analytics_4_activeUsers": "activeUsers",
    "google_analytics_4_engagementRate": "engagementRate",
    "google_analytics_4_averageSessionDuration": "averageSessionDuration",
    "google_analytics_4_eventCount": "eventCount",
    "google_analytics_4_keyEvents": "keyEvents",
}
DIM = {
    "google_analytics_4_date": "date",
    "google_analytics_4_sessionDefaultChannelGroup": "channel",
    "google_analytics_4_pagePath": "page",
    "google_analytics_4_deviceCategory": "device",
    "google_analytics_4_eventName": "event",
}

def load(name):
    p = os.path.join(RAW, name)
    with open(p) as f:
        return json.load(f)

def rows_as_dicts(resp):
    """Turn a Porter response {columns:[{id}], rows:[[...]]} into list of dicts keyed by our field names."""
    cols = [c["id"] for c in resp["columns"]]
    out = []
    for r in resp.get("rows", []):
        rec = {}
        for cid, val in zip(cols, r):
            if cid in DIM:
                rec[DIM[cid]] = val
            elif cid in MET:
                try: rec[MET[cid]] = float(val)
                except (TypeError, ValueError): rec[MET[cid]] = 0.0
        out.append(rec)
    return out

def isod(yyyymmdd):
    return yyyymmdd[:4] + "-" + yyyymmdd[4:6] + "-" + yyyymmdd[6:]

def base(rec):
    """common metric shell with reconstructed engaged & total duration"""
    s = rec.get("sessions", 0.0)
    return {
        "sessions": round(s),
        "activeUsers": round(rec.get("activeUsers", 0.0)),
        "engaged": round(rec.get("engagementRate", 0.0) * s),
        "dur": round(rec.get("averageSessionDuration", 0.0) * s),
        "eventCount": round(rec.get("eventCount", 0.0)),
        "keyEvents": round(rec.get("keyEvents", 0.0)),
    }

def empty():
    return {"sessions":0,"activeUsers":0,"engaged":0,"dur":0,"eventCount":0,"keyEvents":0}

def acc(a, b):
    for k in a: a[k] += b[k]

def main():
    # ---- day x channel backbone ----
    dc_raw = rows_as_dicts(load("daily_channel.json"))
    dailyChan = []
    for r in dc_raw:
        if not r.get("date") or r.get("channel") is None: continue
        dailyChan.append({"date": isod(r["date"]), "channel": r["channel"], **base(r)})
    if not dailyChan:
        print("ERROR: daily_channel.json produced no rows", file=sys.stderr); sys.exit(1)

    dates = sorted({r["date"] for r in dailyChan})
    dailyStart, dailyEnd = dates[0], dates[-1]
    curEnd = dailyEnd
    curStart = (datetime.date.fromisoformat(curEnd) - datetime.timedelta(days=27)).isoformat()
    prevEnd = (datetime.date.fromisoformat(curStart) - datetime.timedelta(days=1)).isoformat()
    prevStart = (datetime.date.fromisoformat(prevEnd) - datetime.timedelta(days=27)).isoformat()

    # ---- derive channelCur / channelPrev (28d source totals) from the backbone ----
    curM, prevM = {}, {}
    for r in dailyChan:
        tgt = curM if curStart <= r["date"] <= curEnd else (prevM if prevStart <= r["date"] <= prevEnd else None)
        if tgt is None: continue
        e = tgt.setdefault(r["channel"], empty()); acc(e, r)
    channelCur = [{"channel": c, **v} for c, v in sorted(curM.items(), key=lambda kv: -kv[1]["sessions"])]
    channelPrev = [{"channel": c, **v} for c, v in prevM.items()]

    # ---- page x channel ----
    def pagechan(name):
        out = []
        for r in rows_as_dicts(load(name)):
            if r.get("page") is None or r.get("channel") is None: continue
            out.append({"page": r["page"], "channel": r["channel"], **base(r)})
        return out
    pageChanCur = pagechan("pagechan_cur.json")
    pageChanPrev = pagechan("pagechan_prev.json")

    # ---- devices ----
    def devices(name):
        out = []
        for r in rows_as_dicts(load(name)):
            if r.get("device") is None: continue
            out.append({"device": r["device"], **base(r)})
        return out
    deviceCur = devices("device_cur.json")
    devicePrev = devices("device_prev.json")

    # ---- key events by name ----
    def keyevents(name):
        out = []
        for r in rows_as_dicts(load(name)):
            if r.get("event") is None: continue
            kv = round(r.get("keyEvents", 0.0))
            if kv <= 0: continue
            out.append({"event": r["event"], "keyEvents": kv, "eventCount": round(r.get("eventCount", 0.0))})
        return out
    keyEventCur = keyevents("keyevent_cur.json")
    keyEventPrev = keyevents("keyevent_prev.json")

    data = {
        "meta": {
            "property": "SurveySparrow — surveysparrow.com (GA4 323271647)",
            "curStart": curStart, "curEnd": curEnd,
            "prevStart": prevStart, "prevEnd": prevEnd,
            "dailyStart": dailyStart, "dailyEnd": dailyEnd,
            "pulled": datetime.date.today().isoformat(), "live": True,
        },
        "dailyChan": dailyChan,
        "channelCur": channelCur, "channelPrev": channelPrev,
        "pageChanCur": pageChanCur, "pageChanPrev": pageChanPrev,
        "deviceCur": deviceCur, "devicePrev": devicePrev,
        "keyEventCur": keyEventCur, "keyEventPrev": keyEventPrev,
    }

    with open(TPL) as f:
        tpl = f.read()
    html = tpl.replace("__GA4_DATA_PLACEHOLDER__", json.dumps(data, separators=(",", ":")))
    with open(OUT, "w") as f:
        f.write(html)

    print("Built index.html")
    print(f"  window: {curStart}..{curEnd}  vs  {prevStart}..{prevEnd}")
    print(f"  backbone: {dailyStart}..{dailyEnd}  ({len(dates)} days)")
    print(f"  dailyChan={len(dailyChan)} channels={len(channelCur)} "
          f"pagesCur={len(pageChanCur)} devices={len(deviceCur)} keyEvents={len(keyEventCur)}")
    print(f"  output bytes: {len(html)}")

if __name__ == "__main__":
    main()
