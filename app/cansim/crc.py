"""CAN CRC-15（生成多项式 x^15 + x^14 + x^10 + x^8 + x^7 + x^4 + x^3 + 1）。"""

# x^15 项隐含在移位反馈中；低 15 位系数:
# bit14,10,8,7,4,3,0 -> 0x4599
CRC15_POLY = 0x4599


def crc15(bits) -> int:
    """对给定比特流（不含 CRC 字段）计算 CAN CRC-15。

    15 位线性反馈移位寄存器，初始值为 0：
    反馈位 = 寄存器 bit14 XOR 输入位；反馈位为 1 时低 15 位异或多项式。
    """
    crc = 0
    for bit in bits:
        feedback = ((crc >> 14) & 1) ^ (bit & 1)
        crc = ((crc << 1) & 0x7FFF)
        if feedback:
            crc ^= CRC15_POLY
    return crc
