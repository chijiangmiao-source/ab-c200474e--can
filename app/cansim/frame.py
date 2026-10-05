"""CAN 2.0A 标准数据帧的字段布局与位填充。"""

from dataclasses import dataclass, field

# 逻辑字段定义（顺序即未填充位流顺序）：字段名 -> 位数
# SOF | 仲裁场(ID+RTR) | 控制场(IDE+r0+DLC) | 数据场 | CRC场(CRC+界定符)
# | 应答场(ACK槽+界定符) | EOF | 帧间隔 IFS
STANDARD_FRAME_FIELDS = [
    ("SOF", 1),       # 帧起始，显性
    ("ID", 11),       # 11 位标准标识符
    ("RTR", 1),       # 远程请求位，数据帧为显性 0
    ("IDE", 1),       # 标识符扩展位，标准帧为显性 0
    ("R0", 1),        # 保留位
    ("DLC", 4),       # 数据长度码
    ("DATA", None),   # 变长：DLC * 8
    ("CRC", 15),      # CRC 序列
    ("CRCDEL", 1),    # CRC 界定符，隐性
    ("ACK", 1),       # ACK 槽，发送方发隐性，接收方填显性
    ("ACKDEL", 1),    # ACK 界定符，隐性
    ("EOF", 7),       # 帧结束，全隐性
    ("IFS", 3),       # 帧间隔，全隐性
]

# 参与位填充与 CRC 计算的字段范围：SOF .. CRC 序列
STUFFED_FIELDS = ("SOF", "ID", "RTR", "IDE", "R0", "DLC", "DATA", "CRC")

FIELD_LABELS = {
    "SOF": "SOF 帧起始",
    "ID": "仲裁场标识符",
    "RTR": "RTR 位",
    "IDE": "IDE 位",
    "R0": "保留位 r0",
    "DLC": "DLC 长度码",
    "DATA": "数据场",
    "CRC": "CRC 序列",
    "CRCDEL": "CRC 界定符",
    "ACK": "ACK 应答槽",
    "ACKDEL": "ACK 界定符",
    "EOF": "帧结束 EOF",
    "IFS": "帧间隔 IFS",
    "STUFF": "位填充",
}


@dataclass
class BitMark:
    """一个（填充后）线上比特的元数据。"""

    field: str
    field_index: int  # 字段内从 1 开始的位序（填充位为填充序号）
    value: int        # 发送方驱动的逻辑电平 0=显性 1=隐性
    stuffed: bool = False


@dataclass
class FrameRequest:
    """审查员录入的一帧标准数据帧请求。"""

    node: str
    time: int          # 请求时刻（比特时间）
    ident: int         # 11 位标准 ID
    dlc: int
    data: list[int] = field(default_factory=list)
    # 故障标注：None / {"kind": "ack"} / {"kind": "crc"} /
    # {"kind": "bit", "position": n}（n 为自 SOF 起的 1 基逻辑位序）
    error: dict | None = None
    seq: int = -1      # 录入序号（校验报错用）


@dataclass
class CanFrame:
    """一帧展开后的逻辑位与填充位。"""

    ident: int
    dlc: int
    data: list[int]
    logical_bits: list[BitMark]
    wire_bits: list[BitMark]

    @classmethod
    def build(cls, ident: int, dlc: int, data: list[int]) -> "CanFrame":
        bits: list[BitMark] = []

        def add(fname: str, values: list[int]):
            for i, v in enumerate(values, start=1):
                bits.append(BitMark(fname, i, v))

        add("SOF", [0])
        # ID 高位先发
        add("ID", [(ident >> (10 - i)) & 1 for i in range(11)])
        add("RTR", [0])
        add("IDE", [0])
        add("R0", [0])
        add("DLC", [(dlc >> (3 - i)) & 1 for i in range(4)])
        data_bits: list[int] = []
        for byte in data:
            # 数据字节高位先发
            data_bits.extend((byte >> (7 - i)) & 1 for i in range(8))
        add("DATA", data_bits)

        # 延迟导入避免循环依赖
        from .crc import crc15

        crc = crc15([b.value for b in bits])
        add("CRC", [(crc >> (14 - i)) & 1 for i in range(15)])
        add("CRCDEL", [1])
        add("ACK", [1])
        add("ACKDEL", [1])
        add("EOF", [1] * 7)
        add("IFS", [1] * 3)

        wire = apply_bit_stuffing(bits)
        return cls(ident, dlc, list(data), bits, wire)

    # 逻辑位（未填充）1 基位置 -> 字段定位
    def locate_logical(self, position: int) -> tuple[str, int]:
        cursor = 0
        for fname, width in STANDARD_FRAME_FIELDS:
            w = self.dlc * 8 if fname == "DATA" else width
            if position <= cursor + w:
                return fname, position - cursor
            cursor += w
        raise ValueError(f"position {position} out of frame")

    def field_span(self, fname: str) -> tuple[int, int]:
        """返回字段在逻辑位流中的 1 基 [起, 止]。"""
        cursor = 0
        for name, width in STANDARD_FRAME_FIELDS:
            w = self.dlc * 8 if name == "DATA" else width
            if name == fname:
                return cursor + 1, cursor + w
            cursor += w
        raise KeyError(fname)


def apply_bit_stuffing(logical: list[BitMark]) -> list[BitMark]:
    """对 SOF..CRC 序列执行 CAN 非归零位填充：连续 5 个同电平后插入互补位。

    填充位带 ``stuffed=True`` 标记；CRC 界定符起不再填充。
    """
    out: list[BitMark] = []
    run = 0
    last = -1
    stuff_no = 0
    stuffing_on = True
    for bm in logical:
        if stuffing_on and bm.field not in STUFFED_FIELDS:
            stuffing_on = False
        if stuffing_on:
            if bm.value == last:
                run += 1
            else:
                run = 1
                last = bm.value
            out.append(bm)
            if run == 5:
                stuff_no += 1
                out.append(
                    BitMark("STUFF", stuff_no, 1 - last, stuffed=True)
                )
                last = 1 - last
                run = 1
        else:
            out.append(bm)
    return out
