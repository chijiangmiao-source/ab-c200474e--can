"""仲裁、错误界定、TEC/REC 与 bus-off 恢复测试。"""

import unittest

from cansim import BusEngine, ValidationError, validate_scenario


def run(payload):
    return BusEngine(payload).run()


class TestArbitration(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "nodes": ["A", "B", "C"],
            "requests": [
                {"time": 0, "node": "A", "id": 0x123, "dlc": 1, "data": [0x11]},
                {"time": 0, "node": "B", "id": 0x124, "dlc": 1, "data": [0x22]},
                {"time": 0, "node": "C", "id": 0x100, "dlc": 1, "data": [0x33]},
            ],
        }
        self.result = run(self.payload)
        self.frame = self.result["frames"][0]

    def test_lowest_id_wins(self):
        self.assertEqual(self.frame["winner"], "C")
        self.assertEqual(self.frame["winner_id"], 0x100)
        self.assertEqual(self.frame["status"], "ok")

    def test_only_winner_advances(self):
        # 同刻三个请求只产生一帧（负方转为接收，不再次推进）
        self.assertEqual(len(self.result["frames"]), 1)

    def test_loser_first_recessive_vs_dominant_location(self):
        losers = {l["node"]: l for l in self.frame["losers"]}
        # ID 0x123 = 001 0010 0011；0x100 = 001 0000 0000
        # 共同前 5 位 00100，第 6 位：0x123=1 而 0x100=0
        self.assertEqual(losers["A"]["field"], "ID")
        self.assertEqual(losers["A"]["field_index"], 6)
        trace = self.frame["trace"]
        bit = trace[losers["A"]["wire_index"] - 1]
        self.assertEqual(bit["bus"], 0)
        self.assertEqual(bit["arbiters"]["A"], 1)
        # 0x124 = 001 0010 0100，同样在第 6 位发送 1
        self.assertEqual(losers["B"]["field_index"], 6)

    def test_ack_slot_dominant_and_tec_decrement(self):
        ack_bits = [b for b in self.frame["trace"] if b["field"] == "ACK"]
        self.assertEqual(len(ack_bits), 1)
        self.assertEqual(ack_bits[0]["bus"], 0)
        self.assertIn("A", ack_bits[0]["drivers"])
        self.assertEqual(self.frame["counters_after"]["C"]["tec"], 0)  # 0 不减到负

    def test_bit_stuffing_visible_in_trace(self):
        # 获胜帧 ID=0x100，SOF 起连续 0 较多，轨迹中应有填充位
        self.assertTrue(any(b["stuffed"] for b in self.frame["trace"]))


class TestFrameFields(unittest.TestCase):
    def test_trace_fields_present_in_order(self):
        result = run({
            "nodes": ["A"],
            "requests": [{"time": 0, "node": "A", "id": 0x555, "dlc": 0, "data": []}],
        })
        fields = [b["field"] for b in result["frames"][0]["trace"]]
        for f in ("SOF", "ID", "RTR", "IDE", "R0", "DLC",
                  "CRC", "CRCDEL", "ACK", "ACKDEL", "EOF", "IFS"):
            self.assertIn(f, fields, f)
        # IFS 位于轨迹末尾
        self.assertTrue(all(b["field"] == "IFS" for b in
                            result["frames"][0]["trace"][-3:]))


class TestACKError(unittest.TestCase):
    def test_single_node_ack_error(self):
        result = run({
            "nodes": ["A"],
            "requests": [{"time": 0, "node": "A", "id": 0x100, "dlc": 0,
                          "data": [], "error": {"kind": "ack"}}],
        })
        f = result["frames"][0]
        self.assertEqual(f["status"], "ack_error")
        self.assertEqual(f["error"]["first_violation"]["field"], "ACK")
        self.assertEqual(f["counters_after"]["A"]["tec"], 8)
        tail = [b["field"] for b in f["trace"]]
        self.assertEqual(tail[-1], "IFS")
        # 主动错误标志 6 显性
        flags = [b for b in f["trace"] if b["field"] == "ERRORFLAG"]
        self.assertEqual(len(flags), 6)
        self.assertTrue(all(b["bus"] == 0 for b in flags))
        self.assertTrue(all(b["bus"] == 1 for b in f["trace"]
                            if b["field"] in ("ERRORDEL", "IFS")))


class TestBitError(unittest.TestCase):
    def test_data_bit_error_location_and_count(self):
        result = run({
            "nodes": ["A", "B"],
            "requests": [{"time": 0, "node": "A", "id": 0x200, "dlc": 1,
                          "data": [0x00], "error": {"kind": "bit", "position": 35}}],
        })
        f = result["frames"][0]
        self.assertEqual(f["status"], "bit_error")
        fv = f["error"]["first_violation"]
        self.assertEqual(fv["field"], "DATA")
        self.assertEqual(fv["field_index"], 1)
        bit = f["trace"][fv["wire_index"] - 1]
        self.assertEqual(bit["sent"], 0)
        self.assertEqual(bit["bus"], 1)
        self.assertEqual(f["counters_after"]["A"]["tec"], 8)
        # 接收方 B 检测到被破坏帧，REC+8
        self.assertEqual(f["counters_after"]["B"]["rec"], 8)
        # 帧在数据位截断，之后是错误标志，不再出现 ACK/EOF
        later = {b["field"] for b in f["trace"][fv["wire_index"]:]}
        self.assertNotIn("ACK", later)
        self.assertIn("ERRORFLAG", later)


class TestCRCError(unittest.TestCase):
    def test_crc_error_no_ack_and_flag(self):
        result = run({
            "nodes": ["A", "B"],
            "requests": [{"time": 0, "node": "A", "id": 0x300, "dlc": 1,
                          "data": [0x55], "error": {"kind": "crc"}}],
        })
        f = result["frames"][0]
        self.assertEqual(f["status"], "crc_error")
        fv = f["error"]["first_violation"]
        self.assertEqual(fv["field"], "CRC")
        self.assertEqual(fv["field_index"], 1)
        # 接收方 ACK 槽不应答
        ack = [b for b in f["trace"] if b["field"] == "ACK"][0]
        self.assertEqual(ack["bus"], 1)
        self.assertEqual(f["counters_after"]["A"]["tec"], 8)
        self.assertEqual(f["counters_after"]["B"]["rec"], 8)


class TestPassiveError(unittest.TestCase):
    def test_passive_flag_and_suspend(self):
        result = run({
            "nodes": ["A"],
            "initial_tec": {"A": 127},
            "requests": [{"time": 0, "node": "A", "id": 0x100, "dlc": 0,
                          "data": [], "error": {"kind": "ack"}}],
        })
        f = result["frames"][0]
        self.assertEqual(f["counters_after"]["A"]["tec"], 135)
        self.assertEqual(f["counters_after"]["A"]["state"], "passive")
        self.assertEqual(f["error"]["state_after"], "passive")
        self.assertEqual(f["error"]["flag"]["kind"], "passive")
        flags = [b for b in f["trace"] if b["field"] == "ERRORFLAG"]
        self.assertTrue(all(b["bus"] == 1 for b in flags))  # 被动标志为隐性
        suspend = [b for b in f["trace"] if b["field"] == "SUSPEND"]
        self.assertEqual(len(suspend), 8)  # 挂起发送 8 隐性
        self.assertTrue(all(b["bus"] == 1 for b in suspend))
        # 尾部仍以 IFS 结束
        self.assertTrue(all(b["field"] == "IFS" for b in f["trace"][-3:]))

    def test_passive_sender_with_active_receiver_flag_is_covered(self):
        result = run({
            "nodes": ["A", "B"],
            "initial_tec": {"A": 127},
            "requests": [{"time": 0, "node": "A", "id": 0x200, "dlc": 1,
                          "data": [0x00], "error": {"kind": "bit", "position": 35}}],
        })
        f = result["frames"][0]
        self.assertEqual(f["error"]["flag"]["kind"], "passive")
        flags = [b for b in f["trace"] if b["field"] == "ERRORFLAG"]
        # B 为主动节点，其主动错误标志覆盖 A 的隐性被动标志
        self.assertTrue(all(b["bus"] == 0 for b in flags))
        self.assertIn("B", f["error"]["flag"]["receiver_active_flags"])


class TestBusOff(unittest.TestCase):
    def _scenario(self, second_time):
        return {
            "nodes": ["A", "B"],
            "initial_tec": {"A": 248},
            "requests": [
                {"time": 0, "node": "A", "id": 0x100, "dlc": 0,
                 "data": [], "error": {"kind": "ack"}},
                # bus-off 期间的新请求：被拒绝
                {"time": 500, "node": "A", "id": 0x101, "dlc": 0, "data": []},
                # 恢复完成后的请求
                {"time": second_time, "node": "A", "id": 0x102, "dlc": 0, "data": []},
                {"time": second_time, "node": "B", "id": 0x200, "dlc": 0, "data": []},
            ],
        }

    def test_busoff_reject_and_recovery(self):
        # 帧 1 长度：截断在 ACK 槽。0x100 DLC0：
        # 逻辑帧 47 位，填充位数实际以引擎为准；错误尾 6+8+3=17
        first = run({
            "nodes": ["A"], "initial_tec": {"A": 248},
            "requests": [{"time": 0, "node": "A", "id": 0x100, "dlc": 0,
                          "data": [], "error": {"kind": "ack"}}],
        })["frames"][0]
        frame_end = first["time"] + first["wire_length"]
        # 错误界定符 8 + IFS 3 = 11 => 已计 1 个序列；再等 127*11
        recover_at = frame_end + 127 * 11
        result = run(self._scenario(recover_at))

        self.assertEqual(result["frames"][0]["counters_after"]["A"]["state"], "busoff")
        self.assertEqual(result["frames"][0]["counters_after"]["A"]["tec"], 256)

        # t=500 的请求被拒绝
        rej = result["rejections"]
        self.assertEqual(len(rej), 1)
        self.assertEqual(rej[0]["node"], "A")
        self.assertEqual(rej[0]["reason"], "bus-off")

        # 恢复事件
        self.assertEqual(len(result["recoveries"]), 1)
        self.assertEqual(result["recoveries"][0]["node"], "A")

        # 恢复后的请求参与仲裁且低 ID 获胜
        last = result["frames"][-1]
        self.assertEqual(last["winner"], "A")
        self.assertEqual(last["winner_id"], 0x102)
        self.assertEqual(last["counters_before"]["A"]["tec"], 0)
        self.assertEqual(last["counters_after"]["A"]["state"], "active")

    def test_request_before_recovery_still_rejected(self):
        first = run({
            "nodes": ["A"], "initial_tec": {"A": 248},
            "requests": [{"time": 0, "node": "A", "id": 0x100, "dlc": 0,
                          "data": [], "error": {"kind": "ack"}}],
        })["frames"][0]
        frame_end = first["time"] + first["wire_length"]
        result = run(self._scenario(frame_end + 127 * 11 - 1))
        # 恢复未完成：A 仍 bus-off，最后一帧由 B 获胜且 A 的请求被拒
        self.assertTrue(any(r["request_seq"] == 2 for r in result["rejections"]))
        last = result["frames"][-1]
        self.assertEqual(last["winner"], "B")


class TestScheduling(unittest.TestCase):
    def test_busy_arrival_queues_to_next_cycle(self):
        result = run({
            "nodes": ["A", "B"],
            "requests": [
                {"time": 0, "node": "A", "id": 0x100, "dlc": 0, "data": []},
                # 第一帧传输期间到达：在 IFS 后与新请求一起仲裁
                {"time": 5, "node": "B", "id": 0x200, "dlc": 0, "data": []},
                {"time": 1000, "node": "A", "id": 0x300, "dlc": 0, "data": []},
                {"time": 1000, "node": "B", "id": 0x2FF, "dlc": 0, "data": []},
            ],
        })
        winners = [(f["winner"], f["winner_id"]) for f in result["frames"]]
        self.assertEqual(winners[0], ("A", 0x100))
        # t=5 的请求在第一帧结束后发送（第二周期）
        self.assertEqual(winners[1], ("B", 0x200))
        # t=1000 同刻：0x2FF < 0x300，B 获胜
        self.assertEqual(winners[2], ("B", 0x2FF))


class TestValidationIntegration(unittest.TestCase):
    def test_bad_input_raises_field_errors(self):
        try:
            validate_scenario({
                "nodes": ["A", "A", "", "X", "Y"],
                "initial_tec": {"Z": 5},
                "requests": [
                    {"time": 5, "node": "A", "id": 0x800, "dlc": 1, "data": [1, 2]},
                    {"time": 1, "node": "A", "id": 0x100, "dlc": 0, "data": [],
                     "error": {"kind": "bit", "position": 35}},
                ],
            })
            self.fail("应当校验失败")
        except ValidationError as e:
            fields = {x["field"] for x in e.errors}
            self.assertIn("nodes", fields)          # 超过 4 个
            self.assertIn("nodes[1]", fields)       # 重名
            self.assertIn("nodes[2]", fields)       # 空名
            self.assertIn("initial_tec.Z", fields)  # 未声明节点
            self.assertTrue(any("id" in f for f in fields))
            self.assertTrue(any("data" in f for f in fields))
            self.assertTrue(any("time" in f for f in fields))
            self.assertTrue(any("position" in f for f in fields))

    def test_too_many_requests(self):
        with self.assertRaises(ValidationError) as cm:
            validate_scenario({
                "nodes": ["A"],
                "requests": [
                    {"time": i, "node": "A", "id": 1, "dlc": 0, "data": []}
                    for i in range(25)
                ],
            })
        self.assertIn("requests", {e["field"] for e in cm.exception.errors})


if __name__ == "__main__":
    unittest.main()
