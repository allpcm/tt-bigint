#!/usr/bin/env python3
"""
Golden model for tt_um_pcm_bigint.

Plain Python integers, masked to 128 bits. Written so a student can read it
beside the Verilog and see the same machine twice. Python's arbitrary-precision
ints are what the hardware is emulating with 128 flip-flops and one full adder,
so this is a real reference rather than a reimplementation of the same bug.
"""

W = 128
MASK = (1 << W) - 1

OP_NOP, OP_LDA, OP_LDB, OP_ADD, OP_SUB, OP_SWAP, OP_SHL, OP_OUT = range(8)

OP_NAMES = {OP_NOP: "NOP", OP_LDA: "LDA", OP_LDB: "LDB", OP_ADD: "ADD",
            OP_SUB: "SUB", OP_SWAP: "SWAP", OP_SHL: "SHL", OP_OUT: "OUT"}

# Cycles from the strobe edge to `done` falling, counted by the bench.
CYCLES_SERIAL = 130     # 1 latch + 128 rotations + 1 done
CYCLES_SIMPLE = 2       # 1 execute + 1 done


class BigInt:
    """Reference model. A and B are ints in [0, 2**128)."""

    def __init__(self):
        self.A = 0
        self.B = 0
        self.carry = 0
        self.dout = 0

    # -- flags ------------------------------------------------------------
    @property
    def zero(self):
        return 1 if self.A == 0 else 0

    # -- operations -------------------------------------------------------
    def step(self, op, data=0):
        """Apply one opcode. Returns the cycle count the hardware should take."""
        if op == OP_NOP:
            return 0

        if op == OP_LDA:
            self.A = ((self.A << 8) | (data & 0xFF)) & MASK
        elif op == OP_LDB:
            self.B = ((self.B << 8) | (data & 0xFF)) & MASK
        elif op == OP_ADD:
            t = self.A + self.B
            self.A = t & MASK
            self.carry = 1 if t > MASK else 0
        elif op == OP_SUB:
            # A - B as A + ~B + 1. carry == 1 means no borrow.
            t = self.A + ((~self.B) & MASK) + 1
            self.A = t & MASK
            self.carry = 1 if t > MASK else 0
        elif op == OP_SWAP:
            self.A, self.B = self.B, self.A
        elif op == OP_SHL:
            self.A = (self.A << 1) & MASK
        elif op == OP_OUT:
            self.dout = (self.A >> (W - 8)) & 0xFF
            self.A = ((self.A << 8) | self.dout) & MASK
        else:
            raise ValueError(f"bad opcode {op}")

        return CYCLES_SERIAL if op in (OP_ADD, OP_SUB) else CYCLES_SIMPLE

    # -- host-level helpers, mirrored exactly by the bench ----------------
    def load_a(self, value):
        for i in range(W // 8 - 1, -1, -1):
            self.step(OP_LDA, (value >> (8 * i)) & 0xFF)

    def load_b(self, value):
        for i in range(W // 8 - 1, -1, -1):
            self.step(OP_LDB, (value >> (8 * i)) & 0xFF)

    def read_a(self):
        """Sixteen OUTs. Non-destructive: A is rotated a full 128 bits."""
        v = 0
        for _ in range(W // 8):
            self.step(OP_OUT)
            v = (v << 8) | self.dout
        return v


def fibonacci(n):
    """What the tile does: load A=0, B=1, then repeat {ADD; SWAP} n times.

    After each round A holds F(k). This is the demonstration program, and the
    reason it is worth demonstrating: 64-bit arithmetic overflows at F(93),
    while 128 bits is exact through F(186).
    """
    m = BigInt()
    m.load_a(0)
    m.load_b(1)
    for _ in range(n):
        m.step(OP_ADD)
        m.step(OP_SWAP)
    return m.A


def multiply(a, b):
    """128-bit multiply in terms of the opcodes the tile provides.

    Horner, MSB first: P = 2P + (bit ? multiplicand : 0). The host walks the
    multiplier's bits because it is the host that supplied them. Deliberately
    not in hardware - this is the algorithm a student should write.
    """
    m = BigInt()
    m.load_a(0)
    m.load_b(a)
    for i in range(W - 1, -1, -1):
        m.step(OP_SHL)
        if (b >> i) & 1:
            m.step(OP_ADD)
    return m.A


if __name__ == "__main__":
    print(f"F(93)  = {fibonacci(93)}")
    print(f"F(186) = {fibonacci(186)}   ({len(str(fibonacci(186)))} digits)")
    print(f"2**64  = {1 << 64}")
    # Compare against unbounded Python Fibonacci, not against the model's own
    # masked output - fibonacci(187) wraps, which is the model correctly
    # emulating 128-bit hardware rather than a bug.
    def true_fib(n):
        a, b = 0, 1
        for _ in range(n):
            a, b = b, a + b
        return a

    for n in (0, 1, 2, 10, 93, 185, 186):
        assert fibonacci(n) == true_fib(n), f"F({n}) mismatch"
    assert true_fib(186) < (1 << W) <= true_fib(187), "F(186) is not the last one under 2^128"
    assert fibonacci(187) == true_fib(187) & MASK, "wrap past 2^128 is not clean"
    assert multiply(123456789, 987654321) == 123456789 * 987654321
    assert multiply((1 << 64) - 1, (1 << 63)) == (((1 << 64) - 1) << 63) & MASK
    print("golden model self-checks pass")
