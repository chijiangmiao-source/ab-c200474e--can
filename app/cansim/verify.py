"""一次性验收脚本：构建检查 + 代码测试 + 页面/健康 HTTP 冒烟。

用法：
    python -m cansim.verify                     # 本机临时起服后自测
    python -m cansim.verify --base-url http://web:8080   # 对已运行服务冒烟

覆盖可观察结果：
  1. 正常仲裁：同刻多请求低 ID 获胜、负方“首次隐性 vs 总线显性”定位、ACK 槽显性；
  2. 被动错误：初始 TEC=127 触发 ACK 错误后进入 error-passive，
     错误标志为 6 隐性并跟随 8 位挂起发送；
  3. bus-off 恢复：TEC=256 后新请求被拒，128×11 空闲位后恢复、清零并重新获胜；
  4. 字段级校验：非法 ID / 超长载荷 / 无效错误位置返回 400 且不含旧结论；
  5. 健康检查与页面入口。

进程退出码即验收结论：0 通过，非 0 失败。
"""

import argparse
import compileall
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../app
PKG_DIR = os.path.join(APP_DIR, "cansim")
ROOT_DIR = os.path.dirname(APP_DIR)                                  # 仓库根
TESTS_DIR = os.path.join(ROOT_DIR, "tests")


class CheckFailure(Exception):
    pass


class Reporter:
    def __init__(self):
        self.failures = []
        self.step_no = 0

    def step(self, title):
        self.step_no += 1
        print(f"\n=== 步骤 {self.step_no}: {title} ===", flush=True)

    def check(self, cond, ok_msg, fail_msg):
        if cond:
            print(f"  [PASS] {ok_msg}", flush=True)
        else:
            print(f"  [FAIL] {fail_msg}", flush=True)
            self.failures.append(fail_msg)
        return cond


# ---------------------------------------------------------------------- #
# HTTP 工具
# ---------------------------------------------------------------------- #
def http_get(base, path, timeout=5):
    req = urllib.request.Request(base.rstrip("/") + path)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def http_post_json(base, path, obj, timeout=5):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


# ---------------------------------------------------------------------- #
# 各验收环节
# ---------------------------------------------------------------------- #
def check_build(r):
    r.step("构建检查：编译全部 Python 源码与测试")
    ok = compileall.compile_dir(PKG_DIR, quiet=1, maxlevels=10)
    ok = compileall.compile_dir(TESTS_DIR, quiet=1, maxlevels=10) and ok
    r.check(ok, "compileall 通过", "compileall 发现语法错误")


def check_unittests(r):
    r.step("代码测试：unittest 全量")
    loader = unittest.TestLoader()
    suite = loader.discover(TESTS_DIR, pattern="test_*.py", top_level_dir=ROOT_DIR)
    stream = open(os.devnull, "w")
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    stream.close()
    r.check(
        result.wasSuccessful(),
        f"全部 {result.testsRun} 项测试通过",
        f"测试失败：{len(result.failures)} 失败 / {len(result.errors)} 错误"
        + "；".join(str(c[0]) for c in result.failures + result.errors),
    )


def check_health_and_page(r, base):
    r.step("HTTP 冒烟：健康响应与页面入口")
    try:
        status, body = http_get(base, "/health")
        health = json.loads(body)
        r.check(status == 200 and health.get("status") == "ok",
                f"GET /health -> 200 {health}", "健康检查失败")
    except Exception as e:  # noqa: BLE001
        r.check(False, "", f"GET /health 异常：{e}")
        return

    status, body = http_get(base, "/")
    text = body.decode("utf-8")
    r.check(status == 200 and "CAN" in text and "逐位" in text,
            "GET / 返回审查页面", "页面入口异常")

    status, body = http_get(base, "/static/app.js")
    r.check(status == 200 and b"runSim" in body,
            "静态资源 /static/app.js 可访问", "静态资源异常")

    status, body = http_get(base, "/api/config")
    cfg = json.loads(body)
    r.check(cfg["max_nodes"] == 4 and cfg["max_requests"] == 24
            and cfg["recovery_sequences"] == 128,
            "/api/config 配置正确", "/api/config 内容异常")


def check_normal_arbitration(r, base):
    r.step("场景验收（一）：正常仲裁 + ACK 应答")
    payload = {
        "nodes": ["A", "B", "C"],
        "requests": [
            {"time": 0, "node": "A", "id": 0x123, "dlc": 1, "data": [0x11]},
            {"time": 0, "node": "B", "id": 0x124, "dlc": 1, "data": [0x22]},
            {"time": 0, "node": "C", "id": 0x100, "dlc": 1, "data": [0x33]},
        ],
    }
    status, data = http_post_json(base, "/api/simulate", payload)
    if not r.check(status == 200 and "result" in data,
                   "仿真接口 200", f"仿真接口异常 status={status}"):
        return
    result = data["result"]
    f = result["frames"][0]
    r.check(f["winner"] == "C" and f["winner_id"] == 0x100 and f["status"] == "ok",
            "同刻请求仅低标识符 0x100 获胜", f"获胜方异常：{f['winner']}")
    r.check(len(result["frames"]) == 1,
            "同刻负方不重复推进，仅产生 1 帧", f"帧数异常：{len(result['frames'])}")
    losers = {l["node"]: l for l in f["losers"]}
    ok_loc = (losers["A"]["field"] == "ID" and losers["A"]["field_index"] == 6
              and losers["A"]["wire_index"] == 7)
    r.check(ok_loc,
            "负方 A 定位：ID 第 6 位（线上第 7 位）首发隐性而总线显性",
            f"负方定位异常：{losers.get('A')}")
    ack = [b for b in f["trace"] if b["field"] == "ACK"][0]
    r.check(ack["bus"] == 0 and "A" in ack["drivers"] and "B" in ack["drivers"],
            "ACK 槽由接收方填显性", f"ACK 槽异常：{ack}")
    fields = [b["field"] for b in f["trace"]]
    needed = ["SOF", "ID", "RTR", "IDE", "R0", "DLC", "DATA",
              "CRC", "CRCDEL", "ACK", "ACKDEL", "EOF", "IFS"]
    r.check(all(x in fields for x in needed),
            "轨迹覆盖 SOF/仲裁/控制/数据/CRC/ACK/EOF/帧间隔",
            "轨迹字段不完整")
    r.check(any(b["stuffed"] for b in f["trace"]),
            "轨迹中包含位填充位", "未见位填充位")


def check_passive(r, base):
    r.step("场景验收（二）：error-passive 被动错误")
    payload = {
        "nodes": ["A"],
        "initial_tec": {"A": 127},
        "requests": [
            {"time": 0, "node": "A", "id": 0x100, "dlc": 0, "data": [],
             "error": {"kind": "ack"}},
        ],
    }
    status, data = http_post_json(base, "/api/simulate", payload)
    if not r.check(status == 200, "仿真接口 200", f"status={status}"):
        return
    f = data["result"]["frames"][0]
    after = f["counters_after"]["A"]
    r.check(after["tec"] == 135 and after["state"] == "passive",
            "TEC 127 +8 = 135，节点进入 error-passive",
            f"计数/状态异常：{after}")
    flag = f["error"]["flag"]
    flags = [b for b in f["trace"] if b["field"] == "ERRORFLAG"]
    suspend = [b for b in f["trace"] if b["field"] == "SUSPEND"]
    r.check(flag["kind"] == "passive" and len(flags) == 6
            and all(b["bus"] == 1 for b in flags),
            "被动错误标志为 6 个隐性位", "被动错误标志异常")
    r.check(len(suspend) == 8 and all(b["bus"] == 1 for b in suspend),
            "被动标志后存在 8 位挂起发送", "挂起发送场异常")
    r.check(f["error"]["first_violation"]["field"] == "ACK",
            "首个违规证据定位 ACK 槽", "首个违规证据异常")


def check_busoff_recovery(r, base):
    r.step("场景验收（三）：bus-off 拒绝请求与 128×11 位恢复")
    # 先发一帧确认帧长，据此计算恢复时刻
    pre = {
        "nodes": ["A"],
        "initial_tec": {"A": 248},
        "requests": [
            {"time": 0, "node": "A", "id": 0x100, "dlc": 0, "data": [],
             "error": {"kind": "ack"}},
        ],
    }
    status, data = http_post_json(base, "/api/simulate", pre)
    if not r.check(status == 200, "前置仿真 200", f"status={status}"):
        return
    first = data["result"]["frames"][0]
    frame_end = first["time"] + first["wire_length"]
    recover_at = frame_end + 127 * 11

    payload = {
        "nodes": ["A", "B"],
        "initial_tec": {"A": 248},
        "requests": [
            {"time": 0, "node": "A", "id": 0x100, "dlc": 0, "data": [],
             "error": {"kind": "ack"}},
            {"time": frame_end + 50, "node": "A", "id": 0x101,
             "dlc": 0, "data": []},
            {"time": recover_at, "node": "A", "id": 0x102, "dlc": 0, "data": []},
            {"time": recover_at, "node": "B", "id": 0x200, "dlc": 0, "data": []},
        ],
    }
    status, data = http_post_json(base, "/api/simulate", payload)
    if not r.check(status == 200, "仿真接口 200", f"status={status}"):
        return
    result = data["result"]
    f0 = result["frames"][0]
    r.check(f0["counters_after"]["A"]["tec"] == 256
            and f0["counters_after"]["A"]["state"] == "busoff",
            "TEC 达 256，节点进入 bus-off",
            f"未进入 bus-off：{f0['counters_after']['A']}")
    rej = result["rejections"]
    r.check(len(rej) == 1 and rej[0]["node"] == "A" and "128" in rej[0]["message"],
            "bus-off 期间新请求被拒绝并提示恢复进度",
            f"拒绝记录异常：{rej}")
    rec = result["recoveries"]
    r.check(len(rec) == 1 and rec[0]["node"] == "A",
            "恰好 128 次 11 位空闲序列后恢复发送资格",
            f"恢复事件异常：{rec}")
    last = result["frames"][-1]
    r.check(last["winner"] == "A" and last["winner_id"] == 0x102
            and last["counters_before"]["A"]["tec"] == 0
            and last["counters_after"]["A"]["state"] == "active",
            "恢复后 TEC 清零，A 以更低 ID 重新仲裁获胜",
            f"恢复后行为异常：winner={last['winner']} "
            f"tec_before={last['counters_before']['A']}")


def check_validation(r, base):
    r.step("场景验收（四）：字段级校验失败并清除旧结论")
    bad = {
        "nodes": ["A", "A"],
        "requests": [
            {"time": 0, "node": "A", "id": 0x800, "dlc": 1, "data": [1, 2]},
            {"time": 0, "node": "A", "id": 0x100, "dlc": 0, "data": [],
             "error": {"kind": "bit", "position": 35}},
            {"time": 0, "node": "A", "id": 0x100, "dlc": 9, "data": []},
        ],
    }
    status, data = http_post_json(base, "/api/simulate", bad)
    r.check(status == 400 and "errors" in data and "result" not in data,
            "非法输入返回 400 且响应不含任何旧结论",
            f"校验响应异常 status={status} keys={list(data)}")
    fields = {e["field"] for e in data.get("errors", [])}
    for fld, label in [
        ("nodes[1]", "重复节点名"),
        ("requests[0].id", "非法帧标识"),
        ("requests[0].data", "超出 DLC 的载荷"),
        ("requests[1].error.position", "DLC=0 的无效数据位位置"),
        ("requests[2].dlc", "越界 DLC"),
    ]:
        r.check(fld in fields, f"字段级反馈包含 {label}（{fld}）",
                f"缺少字段反馈：{label}（{fld}），实际 {sorted(fields)}")


# ---------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="CAN 审查回放一次性验收")
    ap.add_argument("--base-url", default=None,
                    help="已运行服务地址；缺省时本机临时起服")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    base = args.base_url
    own_server = None
    if not base:
        sys.path.insert(0, APP_DIR)
        from cansim.server import build_server

        own_server = build_server(args.host, 0)
        port = own_server.server_address[1]
        t = threading.Thread(target=own_server.serve_forever, daemon=True)
        t.start()
        base = f"http://{args.host}:{port}"
        time.sleep(0.2)

    print(f"# 星载 CAN 审查回放验收 @ {base}", flush=True)
    r = Reporter()
    try:
        check_build(r)
        check_unittests(r)
        check_health_and_page(r, base)
        check_normal_arbitration(r, base)
        check_passive(r, base)
        check_busoff_recovery(r, base)
        check_validation(r, base)
    finally:
        if own_server is not None:
            own_server.shutdown()

    print("\n================ 验收总结 ================", flush=True)
    if r.failures:
        print(f"结果：失败（{len(r.failures)} 项未通过）", flush=True)
        for m in r.failures:
            print(f"  - {m}", flush=True)
        sys.exit(1)
    print("结果：全部通过 ✔", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
