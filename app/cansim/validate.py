"""场景录入的字段级校验。

校验失败时抛出 :class:`ValidationError`，其中 ``errors`` 为
``[{"field": "requests[3].error.position", "message": "..."}]``，
便于前端在对应字段下给出反馈并清除旧结论。
"""

MAX_NODES = 4
MAX_REQUESTS = 24
DATA_FIELD_START = 35  # SOF1+ID11+RTR1+IDE1+R01+DLC4 之后数据场起始逻辑位


class ValidationError(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(e["message"] for e in errors))


def _err(errors, field, message):
    errors.append({"field": field, "message": message})


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate_scenario(payload):
    """校验场景 JSON，返回规范化后的字典；失败抛 :class:`ValidationError`。"""
    errors: list[dict] = []

    if not isinstance(payload, dict):
        raise ValidationError([{"field": "$", "message": "请求体必须是 JSON 对象"}])

    # ---- 节点 ----
    nodes_raw = payload.get("nodes")
    nodes: list[str] = []
    if not isinstance(nodes_raw, list) or not nodes_raw:
        _err(errors, "nodes", "至少需要 1 个节点，至多 4 个")
    else:
        if len(nodes_raw) > MAX_NODES:
            _err(errors, "nodes", f"节点数量至多 {MAX_NODES} 个")
        seen = set()
        for i, n in enumerate(nodes_raw[: MAX_NODES + 1]):
            if not isinstance(n, str) or not n.strip():
                _err(errors, f"nodes[{i}]", "节点名不能为空")
            elif n in seen:
                _err(errors, f"nodes[{i}]", f"节点名 {n!r} 重复")
            else:
                seen.add(n)
                nodes.append(n)

    # ---- 初始发送错误计数 ----
    tec_raw = payload.get("initial_tec", {})
    initial_tec: dict[str, int] = {}
    if tec_raw is not None:
        if not isinstance(tec_raw, dict):
            _err(errors, "initial_tec", "初始 TEC 必须是 {节点: 计数} 对象")
        else:
            for k, v in tec_raw.items():
                if nodes and k not in nodes:
                    _err(errors, f"initial_tec.{k}", "该节点未在 nodes 中声明")
                if not _is_int(v) or v < 0 or v > 255:
                    _err(errors, f"initial_tec.{k}", "TEC 必须为 0..255 的整数（256 即 bus-off，不允许作为初态）")
                else:
                    initial_tec[k] = v

    # ---- 请求 ----
    reqs_raw = payload.get("requests")
    requests = []
    if not isinstance(reqs_raw, list):
        _err(errors, "requests", "requests 必须是数组")
        reqs_raw = []
    elif len(reqs_raw) > MAX_REQUESTS:
        _err(errors, "requests", f"请求数量至多 {MAX_REQUESTS} 条")

    last_time = None
    for i, r in enumerate(reqs_raw[: MAX_REQUESTS + 1]):
        pfx = f"requests[{i}]"
        if not isinstance(r, dict):
            _err(errors, pfx, "请求必须是对象")
            continue

        # 时刻
        t = r.get("time")
        if not _is_int(t) or t < 0:
            _err(errors, f"{pfx}.time", "时刻必须为非负整数（比特时间）")
        else:
            if last_time is not None and t < last_time:
                _err(errors, f"{pfx}.time", "请求必须按时刻非递减排列")
            last_time = t

        # 节点
        node = r.get("node")
        if not isinstance(node, str) or not node:
            _err(errors, f"{pfx}.node", "必须指定节点")
        elif nodes and node not in nodes:
            _err(errors, f"{pfx}.node", f"节点 {node!r} 未声明")

        # 标识符
        ident = r.get("id")
        if isinstance(ident, str):
            try:
                ident = int(ident, 0)
            except ValueError:
                ident = None
        if not _is_int(ident) or not (0 <= ident <= 0x7FF):
            _err(errors, f"{pfx}.id", "帧标识非法：必须是 0..0x7FF 的 11 位标准 ID")
            ident_ok = False
        else:
            ident_ok = True

        # DLC
        dlc = r.get("dlc")
        if not _is_int(dlc) or not (0 <= dlc <= 8):
            _err(errors, f"{pfx}.dlc", "DLC 必须为 0..8 的整数")
            dlc = None

        # 数据载荷
        data = r.get("data", [])
        if not isinstance(data, list):
            _err(errors, f"{pfx}.data", "data 必须是字节数组")
            data = []
        if dlc is not None:
            if len(data) > dlc:
                _err(errors, f"{pfx}.data", f"载荷长度 {len(data)} 超出 DLC={dlc}")
            elif len(data) < dlc:
                _err(errors, f"{pfx}.data", f"载荷长度 {len(data)} 短于 DLC={dlc}")
        for j, b in enumerate(data):
            if not _is_int(b) or not (0 <= b <= 255):
                _err(errors, f"{pfx}.data[{j}]", "数据字节必须为 0..255 的整数")

        # 错误标注
        err = r.get("error")
        if err is not None:
            if not isinstance(err, dict):
                _err(errors, f"{pfx}.error", "错误标注必须是对象或 null")
            else:
                kind = err.get("kind")
                if kind not in ("ack", "crc", "bit"):
                    _err(errors, f"{pfx}.error.kind", "错误类型只能是 ack / crc / bit")
                else:
                    allowed = {"kind"}
                    unknown = set(err) - allowed - ({"position"} if kind == "bit" else set())
                    for u in sorted(unknown):
                        _err(errors, f"{pfx}.error.{u}", "非法的错误标注字段")
                    if kind == "bit":
                        pos = err.get("position")
                        # 仅允许标注数据场：逻辑位 35 .. 34+8*DLC
                        data_lo = DATA_FIELD_START
                        data_hi = 34 + 8 * (dlc or 0)
                        if not _is_int(pos):
                            _err(errors, f"{pfx}.error.position", "位错误位置必须是整数")
                        elif (dlc or 0) == 0:
                            _err(errors, f"{pfx}.error.position",
                                 "DLC=0 的帧没有数据位，无法标注数据位错误")
                        elif not (data_lo <= pos <= data_hi):
                            _err(
                                errors,
                                f"{pfx}.error.position",
                                f"位错误位置 {pos} 超出数据场范围"
                                f" {data_lo}..{data_hi}（自 SOF 起的逻辑位序，"
                                f"DLC={dlc or 0}）",
                            )

        if ident_ok and dlc is not None and len(data) == dlc and all(
            _is_int(b) and 0 <= b <= 255 for b in data
        ):
            requests.append(
                {
                    "time": t if _is_int(t) else 0,
                    "node": node or "",
                    "id": ident,
                    "dlc": dlc,
                    "data": data,
                    "error": err if isinstance(err, dict) and err.get("kind") in ("ack", "crc", "bit") else None,
                }
            )

    if errors:
        raise ValidationError(errors)

    return {"nodes": nodes, "initial_tec": initial_tec, "requests": requests}
