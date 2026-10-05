"""CAN 2.0A 总线逐位仿真引擎。

建模范围（ISO 11898-1 经典 CAN）：

* 非归零码 + 位填充（SOF..CRC 序列，连续 5 同电平后插入互补位）；
* 线与总线：显性 0 覆盖隐性 1；
* 逐位仲裁：同刻（含总线忙期间排队、在 IFS 后同时参与）请求中
  最低 ID 获胜，负方可定位到“首次发送隐性而总线显性”的位；
* 三类可标注故障：ACK 错误、CRC 错误、数据场指定位错误；
* 错误界定：主动错误标志（6 显性）/ 被动错误标志（6 隐性）、
  错误界定符（8 隐性）、被动发送方挂起发送（8 隐性）、帧间隔（3 隐性）；
* 故障界定：TEC/REC；计数 >=128 为 error-passive，TEC>=256 为 bus-off；
  成功发送 TEC-1、成功接收 REC-1；发送检测错误 TEC+8；
  接收方因 CRC/位错误发送主动错误标志 REC+8；
* bus-off 节点不参与仲裁、新请求被拒绝，监测到 128 次
  11 位连续隐性序列后恢复（TEC=REC=0，回到 error-active）。
"""

from dataclasses import dataclass

from .frame import CanFrame
from .validate import validate_scenario

TEC_PASSIVE = 128
TEC_BUSOFF = 256
IDLE_SEQ_BITS = 11
RECOVERY_SEQUENCES = 128
ERROR_FLAG_BITS = 6
ERROR_DELIM_BITS = 8
SUSPEND_BITS = 8
IFS_BITS = 3
DATA_FIELD_START = 35  # SOF1+ID11+RTR1+IDE1+R01+DLC4 之后数据场起始逻辑位


@dataclass
class NodeState:
    name: str
    tec: int = 0
    rec: int = 0
    state: str = "active"      # active / passive / busoff
    recovery_count: int = 0    # bus-off 期间已监测到的 11 位隐性序列数

    def snapshot(self):
        return {"tec": self.tec, "rec": self.rec, "state": self.state}


class BusEngine:
    def __init__(self, payload):
        self.scenario = validate_scenario(payload)
        self.nodes: dict[str, NodeState] = {}
        for n in self.scenario["nodes"]:
            tec = self.scenario["initial_tec"].get(n, 0)
            self.nodes[n] = NodeState(
                n, tec=tec, state="passive" if tec >= TEC_PASSIVE else "active"
            )
        self.bus_time = 0          # 总线已占用到的时刻（下一位的时刻）
        self.idle_run = 0          # 当前连续隐性位计数
        self.recovery_events: list[dict] = []

    # ------------------------------------------------------------------ #
    # 电平观察与 bus-off 恢复
    # ------------------------------------------------------------------ #
    def _observe(self, bus_bit: int, time: int):
        if bus_bit == 1:
            self.idle_run += 1
            if self.idle_run % IDLE_SEQ_BITS == 0:
                for st in self.nodes.values():
                    if st.state == "busoff":
                        st.recovery_count += 1
                        if st.recovery_count >= RECOVERY_SEQUENCES:
                            st.tec = 0
                            st.rec = 0
                            st.state = "active"
                            st.recovery_count = 0
                            self.recovery_events.append(
                                {
                                    "time": time,
                                    "node": st.name,
                                    "message": (
                                        f"节点 {st.name} 已监测 128 次 11 位连续隐性序列，"
                                        f"TEC/REC 清零，恢复发送资格"
                                    ),
                                }
                            )
        else:
            self.idle_run = 0

    def _idle(self, start: int, nbits: int):
        for k in range(nbits):
            self._observe(1, start + k)
        self.bus_time = start + nbits

    # ------------------------------------------------------------------ #
    # 请求调度
    # ------------------------------------------------------------------ #
    def run(self):
        groups: dict[int, list[dict]] = {}
        for i, r in enumerate(self.scenario["requests"]):
            groups.setdefault(r["time"], []).append({**r, "seq": i})
        times = sorted(groups)

        frames_out = []
        rejections = []
        idle_spans = []
        gi = 0

        while gi < len(times):
            t = times[gi]
            if t > self.bus_time:
                start = self.bus_time
                self._idle(start, t - self.bus_time)
                idle_spans.append(
                    {"start": start, "end": self.bus_time,
                     "length": t - self.bus_time}
                )
            # 总线忙期间到达的请求一并在当前 IFS 后参与仲裁
            cutoff = self.bus_time
            pending: list[dict] = []
            while gi < len(times) and times[gi] <= cutoff:
                pending.extend(groups[times[gi]])
                gi += 1
            cycle_time = cutoff

            contenders = []
            for r in sorted(pending, key=lambda x: x["seq"]):
                st = self.nodes[r["node"]]
                if st.state == "busoff":
                    rejections.append(
                        {
                            "request_seq": r["seq"],
                            "time": cycle_time,
                            "request_time": r["time"],
                            "node": r["node"],
                            "id": r["id"],
                            "reason": "bus-off",
                            "message": (
                                f"节点 {r['node']} 处于 bus-off（TEC={st.tec}），"
                                f"其请求（ID=0x{r['id']:03X}）被拒绝、不参与仲裁；"
                                f"恢复进度 {st.recovery_count}/128 个 11 位空闲序列"
                            ),
                        }
                    )
                else:
                    contenders.append(r)

            if contenders:
                frames_out.append(self._transmit(cycle_time, contenders))

        return {
            "frames": frames_out,
            "rejections": rejections,
            "idle_spans": idle_spans,
            "recoveries": self.recovery_events,
            "nodes": [
                {"name": n, **self.nodes[n].snapshot(),
                 "recovery_count": self.nodes[n].recovery_count}
                for n in self.scenario["nodes"]
            ],
        }

    # ------------------------------------------------------------------ #
    # 仲裁 + 传输
    # ------------------------------------------------------------------ #
    def _transmit(self, t_start, contenders):
        built = {
            r["seq"]: CanFrame.build(r["id"], r["dlc"], r["data"])
            for r in contenders
        }
        winner_req = min(contenders, key=lambda r: (r["id"], r["seq"]))
        wf = built[winner_req["seq"]]
        arb_end = next(i for i, bm in enumerate(wf.wire_bits) if bm.field == "IDE")

        active = {r["seq"]: r for r in contenders}
        losers = []
        trace: list[dict] = []
        counters_before = {n: self.nodes[n].snapshot() for n in self.nodes}

        def emit(idx0, fname, findex, sent, bus, drivers, stuffed=False,
                 note="", arb=None):
            rec = {
                "index": idx0 + 1,
                "time": t_start + idx0,
                "field": fname,
                "field_index": findex,
                "stuffed": stuffed,
                "sent": sent,
                "bus": bus,
                "drivers": list(drivers),
                "note": note,
                "arbiters": dict(arb) if arb is not None else None,
            }
            trace.append(rec)
            self._observe(bus, t_start + idx0)
            return rec

        # ---- 逐位仲裁：SOF..RTR（含填充位）----
        for i in range(arb_end):
            bm = wf.wire_bits[i]
            arb = {}
            driven = []
            for seq, r in list(active.items()):
                v = built[seq].wire_bits[i].value
                arb[r["node"]] = v
                driven.append((r, v))
            bus = min(v for _, v in driven)
            losers_here = []
            if not bm.stuffed:
                for r, v in driven:
                    if v == 1 and bus == 0 and bm.field in ("SOF", "ID", "RTR"):
                        losers_here.append(r)
            note = ""
            if losers_here:
                names = "、".join(r["node"] for r in losers_here)
                note = f"仲裁失利：{names} 发送隐性(1)而总线为显性(0)"
            emit(i, bm.field, bm.field_index, bm.value, bus,
                 [r["node"] for r, _ in driven], bm.stuffed, note, arb)
            for r in losers_here:
                losers.append(
                    {
                        "node": r["node"],
                        "id": r["id"],
                        "wire_index": i + 1,
                        "field": bm.field,
                        "field_index": bm.field_index,
                        "message": (
                            f"节点 {r['node']}（ID=0x{r['id']:03X}）在仲裁场 "
                            f"{bm.field} 第 {bm.field_index} 位（线上第 {i + 1} 位）"
                            f"首次发送隐性位而总线为显性位，仲裁失利；"
                            f"获胜方 {winner_req['node']}（ID=0x{winner_req['id']:03X}）"
                        ),
                    }
                )
                del active[r["seq"]]

        # ID 完全相同、仲裁场无法区分者：按录入顺序取先，其余记为并列负方
        for seq, r in list(active.items()):
            if seq != winner_req["seq"]:
                losers.append(
                    {
                        "node": r["node"],
                        "id": r["id"],
                        "wire_index": arb_end,
                        "field": "RTR",
                        "field_index": 1,
                        "tie": True,
                        "message": (
                            f"节点 {r['node']} 与获胜方标识符相同"
                            f"（ID=0x{r['id']:03X}），仲裁场未能区分，"
                            f"按录入顺序由 {winner_req['node']} 先行发送"
                        ),
                    }
                )
                del active[seq]

        # ---- 获胜帧传输 ----
        winner_node = winner_req["node"]
        wstate = self.nodes[winner_node]
        receivers = [
            self.nodes[n]
            for n in self.scenario["nodes"]
            if n != winner_node and self.nodes[n].state != "busoff"
        ]

        inject = winner_req.get("error")
        inject_kind = inject["kind"] if inject else None
        crc_wire = next(
            i for i, bm in enumerate(wf.wire_bits)
            if bm.field == "CRC" and bm.field_index == 1
        )
        bit_wire = None
        if inject_kind == "bit":
            data_index = inject["position"] - DATA_FIELD_START + 1
            bit_wire = next(
                i for i, bm in enumerate(wf.wire_bits)
                if bm.field == "DATA" and bm.field_index == data_index
            )

        first_violation = None
        status = "ok"
        cut = None

        for i in range(arb_end, len(wf.wire_bits)):
            bm = wf.wire_bits[i]
            sent = bm.value
            bus = sent
            note = ""
            drivers = [winner_node]

            if i == bit_wire:
                bus = 1 - sent
                note = (
                    f"注入数据位错误（DATA 第 {bm.field_index} 位）："
                    f"发送方驱动 {sent}，总线采样为 {bus}"
                )
                first_violation = {
                    "wire_index": i + 1,
                    "field": "DATA",
                    "field_index": bm.field_index,
                    "sent": sent,
                    "observed": bus,
                    "message": note,
                }
                status = "bit_error"
                cut = i
                emit(i, "DATA", bm.field_index, sent, bus, drivers,
                     bm.stuffed, note)
                break

            if i == crc_wire and inject_kind == "crc":
                sent = 1 - sent
                bus = sent
                note = "注入 CRC 错误：CRC 首位被翻转，接收方校验将失败"
                first_violation = {
                    "wire_index": i + 1,
                    "field": "CRC",
                    "field_index": 1,
                    "sent": sent,
                    "observed": sent,
                    "expected": bm.value,
                    "message": (
                        f"首个违规证据：CRC 序列第 1 位（线上第 {i + 1} 位）"
                        f"发送值 {sent} 与正确 CRC 值 {bm.value} 相反"
                    ),
                }

            if bm.field == "ACK":
                if inject_kind in ("ack", "crc"):
                    bus = 1
                    drivers = []
                    if inject_kind == "ack":
                        note = "标注 ACK 故障：ACK 槽无任何节点填显性，发送方采样到隐性"
                        first_violation = {
                            "wire_index": i + 1,
                            "field": "ACK",
                            "field_index": 1,
                            "sent": 1,
                            "observed": 1,
                            "message": "ACK 错误：ACK 槽保持隐性，发送方未获得应答",
                        }
                        status = "ack_error"
                    else:
                        note = (
                            "接收方 CRC 校验均失败，ACK 槽无人填显性；"
                            "发送方将按 ACK 错误发起错误标志"
                        )
                        status = "crc_error"
                    cut = i
                    emit(i, "ACK", 1, 1, 1, drivers, False, note)
                    break
                ackers = [s.name for s in receivers]
                emit(i, "ACK", 1, 1, 0, [winner_node] + ackers,
                     note=(f"接收方 {'、'.join(ackers) or '（无）'} 在 ACK 槽填显性应答"
                           if ackers else "ACK 槽（无其他接收节点）"))
                continue

            emit(i, bm.field, bm.field_index, sent, bus, drivers,
                 bm.stuffed, note)

        # ---- 收尾 ----
        if status == "ok":
            if wstate.tec > 0:
                wstate.tec -= 1
            for s in receivers:
                if s.rec > 0:
                    s.rec -= 1
            self.bus_time = t_start + len(wf.wire_bits)
            flag_info = None
        else:
            flag_info = self._emit_error_sequence(
                t_start, trace, cut, wstate, receivers, status
            )

        # 状态迁移
        self._update_states(wstate, receivers)

        counters_after = {n: self.nodes[n].snapshot() for n in self.nodes}
        error_info = None
        if status != "ok":
            error_info = {
                "kind": status,
                "source": winner_node,
                "tec_delta": (
                    counters_after[winner_node]["tec"]
                    - counters_before[winner_node]["tec"]
                ),
                "first_violation": first_violation,
                "flag": flag_info,
                "state_after": wstate.state,
            }

        return {
            "request_seq": winner_req["seq"],
            "time": t_start,
            "winner": winner_node,
            "winner_id": winner_req["id"],
            "dlc": winner_req["dlc"],
            "data": winner_req["data"],
            "injected": inject_kind,
            "losers": losers,
            "status": status,
            "acknowledged": status == "ok",
            "error": error_info,
            "counters_before": counters_before,
            "counters_after": counters_after,
            "trace": trace,
            "wire_length": trace[-1]["index"] if trace else 0,
        }

    def _update_states(self, wstate, receivers):
        if wstate.tec >= TEC_BUSOFF:
            if wstate.state != "busoff":
                wstate.state = "busoff"
                wstate.recovery_count = 0
        elif wstate.tec >= TEC_PASSIVE or wstate.rec >= TEC_PASSIVE:
            wstate.state = "passive"
        else:
            wstate.state = "active"
        for s in receivers:
            if s.state == "busoff":
                continue
            if s.rec >= TEC_PASSIVE:
                s.state = "passive"
            elif s.tec < TEC_PASSIVE and s.rec < TEC_PASSIVE:
                s.state = "active"

    def _emit_error_sequence(self, t_start, trace, cut, wstate, receivers, status):
        """错误标志 + 错误界定符 [+ 被动挂起发送] + 帧间隔，并更新计数。"""
        # 计数更新先于标志：进入 passive/bus-off 的阈值以检测后计数判定
        wstate.tec += 8
        if status == "crc_error":
            for s in receivers:
                s.rec += 8
        # 数据位错误：接收方同样因收到被破坏的帧而发送错误标志
        if status == "bit_error":
            for s in receivers:
                s.rec += 8
        self._update_states(wstate, receivers)

        sender_passive = wstate.state == "passive"
        entering_busoff = wstate.state == "busoff"
        # 主动接收方在 CRC/位错误场景下发主动标志，可覆盖被动发送方的隐性标志
        rx_active = [
            s.name for s in receivers
            if s.state == "active" and status in ("crc_error", "bit_error")
        ]

        if entering_busoff:
            # TEC 达 256 前必为 error-passive，最后一个标志按被动（6 隐性）发出
            flag_kind = "busoff-entry"
            polarity = 1
        else:
            polarity = 1 if sender_passive else 0
            flag_kind = "passive" if sender_passive else "active"
        bus_flag = 0 if rx_active else polarity
        drivers = rx_active if (polarity == 1 and rx_active) else [wstate.name]

        notes = {
            "bit_error": "发送方检测到位错误，发起{}错误标志".format(
                "被动（隐性，不影响总线）" if (sender_passive or entering_busoff)
                else "主动（6 显性）"),
            "ack_error": "发送方检测到 ACK 错误，发起{}错误标志（自 ACK 界定符位置起）".format(
                "被动（隐性）" if (sender_passive or entering_busoff)
                else "主动（6 显性）"),
            "crc_error": "发送方未收到应答，发起{}错误标志；接收方另在 EOF 处发标志".format(
                "被动（隐性）" if (sender_passive or entering_busoff)
                else "主动（6 显性）"),
        }
        if entering_busoff:
            notes[status] += "；TEC 达 256，标志结束后节点进入 bus-off，开始监测 128×11 隐性位"

        idx = cut  # 0 基，后续位从 cut+1 开始
        for k in range(ERROR_FLAG_BITS):
            idx += 1
            trace.append({
                "index": idx + 1,
                "time": t_start + idx,
                "field": "ERRORFLAG",
                "field_index": k + 1,
                "stuffed": False,
                "sent": polarity,
                "bus": bus_flag,
                "drivers": list(drivers),
                "note": (notes[status] if k == 0 else "")
                        + ("" if not rx_active or polarity == 0
                           else f"；总线实际由主动接收方 {'、'.join(rx_active)} 拉为显性"),
                "arbiters": None,
            })
            self._observe(bus_flag, t_start + idx)

        def tail(fname, nbits, note0):
            nonlocal idx
            for k in range(nbits):
                idx += 1
                trace.append({
                    "index": idx + 1,
                    "time": t_start + idx,
                    "field": fname,
                    "field_index": k + 1,
                    "stuffed": False,
                    "sent": 1,
                    "bus": 1,
                    "drivers": [],
                    "note": note0 if k == 0 else "",
                    "arbiters": None,
                })
                self._observe(1, t_start + idx)

        if entering_busoff:
            # 恢复计时从错误界定符起：界定符 8 + IFS 3 即第 1 个 11 位隐性序列
            self.idle_run = 0
        tail("ERRORDEL", ERROR_DELIM_BITS, "错误界定符（8 隐性）")
        if sender_passive and not entering_busoff:
            tail("SUSPEND", SUSPEND_BITS,
                 "错误被动发送方挂起发送（8 隐性）")
        tail("IFS", IFS_BITS, "帧间隔（3 隐性）")

        self.bus_time = t_start + idx + 1
        return {
            "kind": flag_kind,
            "bits": ERROR_FLAG_BITS,
            "polarity": polarity,
            "drivers": drivers,
            "receiver_active_flags": rx_active,
        }
