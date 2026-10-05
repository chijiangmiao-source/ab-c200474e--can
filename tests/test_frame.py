"""CRC-15 与帧结构测试。"""

import unittest

from cansim import CanFrame, crc15
from cansim.frame import apply_bit_stuffing, BitMark


class TestCRC(unittest.TestCase):
    def test_zeros(self):
        self.assertEqual(crc15([0] * 34), 0)

    def test_known_id_dlc_data(self):
        # ID=0x000, DLC=0：SOF..DLC 的逻辑位全 0，CRC 应为 0
        f = CanFrame.build(0x000, 0, [])
        crc_bits = [b.value for b in f.logical_bits if b.field == "CRC"]
        self.assertEqual(crc_bits, [0] * 15)

    def test_crc_changes_with_data(self):
        f1 = CanFrame.build(0x123, 1, [0x00])
        f2 = CanFrame.build(0x123, 1, [0x01])
        c1 = [b.value for b in f1.logical_bits if b.field == "CRC"]
        c2 = [b.value for b in f2.logical_bits if b.field == "CRC"]
        self.assertNotEqual(c1, c2)

    def test_frame_field_layout(self):
        f = CanFrame.build(0x7FF, 2, [0xAA, 0x55])
        ids = [b.value for b in f.logical_bits if b.field == "ID"]
        self.assertEqual(ids, [1] * 11)
        data = [b.value for b in f.logical_bits if b.field == "DATA"]
        self.assertEqual(data, [1, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 1])
        # 尾部固定字段
        self.assertEqual([b.value for b in f.logical_bits if b.field == "EOF"], [1] * 7)
        self.assertEqual([b.value for b in f.logical_bits if b.field == "IFS"], [1] * 3)
        self.assertEqual([b.value for b in f.logical_bits if b.field == "ACK"], [1])


class TestStuffing(unittest.TestCase):
    def test_insert_after_five_equal(self):
        bits = [BitMark("ID", i + 1, 0) for i in range(6)]
        out = apply_bit_stuffing(bits)
        # 5 个 0 后插入一个 1
        self.assertEqual(len(out), 7)
        self.assertTrue(out[5].stuffed)
        self.assertEqual(out[5].value, 1)

    def test_id_0x000_frame_has_stuff_bits(self):
        # SOF + 11 个全 0 ID + RTR/IDE/R0/DLC=0 连续长串 0，应有填充
        f = CanFrame.build(0x000, 0, [])
        stuff = [b for b in f.wire_bits if b.stuffed]
        self.assertGreaterEqual(len(stuff), 2)
        for b in stuff:
            self.assertEqual(b.field, "STUFF")

    def test_no_stuff_after_crc(self):
        f = CanFrame.build(0x000, 0, [])
        seen_crc_delim = False
        for b in f.wire_bits:
            if b.field == "CRCDEL":
                seen_crc_delim = True
            if seen_crc_delim:
                self.assertFalse(b.stuffed)

    def test_stuffing_breaks_six_run(self):
        f = CanFrame.build(0x001, 0, [])
        # 线上任意 6 个连续位窗口（填充区内）不得同电平
        stuffed_region = [b for b in f.wire_bits if b.field != "CRCDEL"][:0]
        region = []
        for b in f.wire_bits:
            region.append(b)
            if b.field == "CRC":
                break
        vals = [b.value for b in region]
        for i in range(len(vals) - 5):
            self.assertFalse(all(v == vals[i] for v in vals[i:i + 6]),
                             f"6 位同电平出现在 {i}")


if __name__ == "__main__":
    unittest.main()
