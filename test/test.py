"""
Cocotb bench for tt_um_pcm_bigint.

Same conventions as the ternary MLP tile, for the same reasons:

    ALL STIMULUS IS DRIVEN ON THE FALLING EDGE.

The DUT samples opcode, strobe and data on the rising edge. Driving on the
rising edge is a race that mostly works in RTL and produces intermittent
gate-level mismatches that look like logic bugs and are not.

Reads raise on X or Z rather than resolving to zero. A resolved X is a silent
false pass, and in a netlist with real cell delays it is exactly what a
stimulus race produces.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

import golden as G

CLK_NS = 20  # 50 MHz, the silicon target

# uio_in bit positions - must match the Verilog
C_OP_LSB = 0   # uio_in[2:0]
C_STROBE = 3
# uio_out bit positions
S_BUSY = 4
S_DONE = 5
S_ZERO = 6
S_CARRY = 7


def _clock(dut):
    """Clock(..., unit=) in cocotb 2.x, units= in 1.x. Support both."""
    try:
        return Clock(dut.clk, CLK_NS, unit="ns")
    except TypeError:
        return Clock(dut.clk, CLK_NS, units="ns")


def bit(sig, n):
    s = str(sig.value)
    c = s[len(s) - 1 - n]
    if c not in "01":
        raise AssertionError(f"{sig._name}[{n}] is '{c}' (full value {s})")
    return int(c)


def byte(sig):
    s = str(sig.value)
    if any(c not in "01" for c in s):
        raise AssertionError(f"{sig._name} is '{s}'")
    return int(s, 2)


async def reset(dut):
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 1)


async def issue(dut, op, data=0):
    """Strobe one opcode and wait for done. Returns (cycles, uo_out byte).

    Cycles are counted from the clock edge that latches the strobe through the
    edge on which done is observed, so they are directly comparable with the
    golden model's CYCLES_SERIAL / CYCLES_SIMPLE.
    """
    await FallingEdge(dut.clk)
    dut.ui_in.value = data & 0xFF
    dut.uio_in.value = (op & 0x7) | (1 << C_STROBE)
    await FallingEdge(dut.clk)
    dut.uio_in.value = 0
    dut.ui_in.value = 0

    cycles = 1
    while True:
        await RisingEdge(dut.clk)
        cycles += 1
        if bit(dut.uio_out, S_DONE):
            return cycles, byte(dut.uo_out)
        assert cycles < 200, f"done never asserted for op {G.OP_NAMES[op]}"


async def load_a(dut, model, value):
    for i in range(15, -1, -1):
        b = (value >> (8 * i)) & 0xFF
        await issue(dut, G.OP_LDA, b)
        model.step(G.OP_LDA, b)


async def load_b(dut, model, value):
    for i in range(15, -1, -1):
        b = (value >> (8 * i)) & 0xFF
        await issue(dut, G.OP_LDB, b)
        model.step(G.OP_LDB, b)


async def read_a(dut, model):
    """Sixteen OUTs, MSB-first. Non-destructive in both DUT and model."""
    v = 0
    for _ in range(16):
        _, d = await issue(dut, G.OP_OUT)
        model.step(G.OP_OUT)
        assert d == model.dout, f"OUT byte: dut 0x{d:02x} model 0x{model.dout:02x}"
        v = (v << 8) | d
    return v


def check_flags(dut, model, where):
    z, c = bit(dut.uio_out, S_ZERO), bit(dut.uio_out, S_CARRY)
    assert z == model.zero, f"{where}: zero dut {z} model {model.zero}"
    assert c == model.carry, f"{where}: carry dut {c} model {model.carry}"


# ---------------------------------------------------------------------------

@cocotb.test()
async def test_load_and_read(dut):
    """Round-trip values through LDA and OUT, and confirm OUT is
    non-destructive - sixteen reads rotate A a full 128 bits."""
    cocotb.start_soon(_clock(dut).start())
    await reset(dut)
    model = G.BigInt()

    vals = [0, 1, G.MASK, 1 << 127, 0x0123456789ABCDEF_FEDCBA9876543210,
            random.Random(1).getrandbits(128)]
    for v in vals:
        await load_a(dut, model, v)
        got = await read_a(dut, model)
        assert got == v, f"round trip: wrote 0x{v:032x} read 0x{got:032x}"
        again = await read_a(dut, model)
        assert again == v, "OUT was destructive - A did not return to position"
    dut._log.info(f"{len(vals)} values round-tripped, OUT non-destructive")


@cocotb.test()
async def test_add_sub(dut):
    """Bit-serial add and subtract against the golden model, including the
    carry-out and borrow cases and the exact 130-cycle latency."""
    cocotb.start_soon(_clock(dut).start())
    await reset(dut)
    rnd = random.Random(20261122)

    cases = [(0, 0), (1, 1), (G.MASK, 1), (G.MASK, G.MASK),
             (1 << 127, 1 << 127), (0, 1), (5, 7),
             ((1 << 64) - 1, 1), (1 << 64, (1 << 64) - 1)]
    cases += [(rnd.getrandbits(128), rnd.getrandbits(128)) for _ in range(12)]

    lat = set()
    for a, b in cases:
        for op in (G.OP_ADD, G.OP_SUB):
            model = G.BigInt()
            await reset(dut)
            await load_a(dut, model, a)
            await load_b(dut, model, b)

            cyc, _ = await issue(dut, op)
            model.step(op)
            lat.add(cyc)
            check_flags(dut, model, f"{G.OP_NAMES[op]} 0x{a:x},0x{b:x}")

            got = await read_a(dut, model)
            assert got == model.A, (
                f"{G.OP_NAMES[op]}: a=0x{a:032x} b=0x{b:032x} "
                f"dut=0x{got:032x} model=0x{model.A:032x}")

    assert lat == {G.CYCLES_SERIAL}, f"serial latency not fixed: {sorted(lat)}"
    dut._log.info(f"{len(cases)} operand pairs x ADD/SUB verified, "
                  f"latency fixed at {G.CYCLES_SERIAL} cycles")


@cocotb.test()
async def test_swap_shl(dut):
    """SWAP and SHL, including the bit that falls off the top of A."""
    cocotb.start_soon(_clock(dut).start())
    await reset(dut)
    rnd = random.Random(7)
    model = G.BigInt()

    for _ in range(6):
        a, b = rnd.getrandbits(128), rnd.getrandbits(128)
        await reset(dut)
        model = G.BigInt()
        await load_a(dut, model, a)
        await load_b(dut, model, b)

        await issue(dut, G.OP_SWAP)
        model.step(G.OP_SWAP)
        assert await read_a(dut, model) == model.A == b, "SWAP did not move B into A"

        for _ in range(3):
            await issue(dut, G.OP_SHL)
            model.step(G.OP_SHL)
        assert await read_a(dut, model) == model.A, "SHL mismatch"

    # a 1 in the top bit must shift out and leave zero
    await reset(dut)
    model = G.BigInt()
    await load_a(dut, model, 1 << 127)
    await issue(dut, G.OP_SHL)
    model.step(G.OP_SHL)
    assert await read_a(dut, model) == 0 == model.A, "top bit did not fall off"
    assert bit(dut.uio_out, S_ZERO) == 1, "zero flag not set after A became 0"
    dut._log.info("SWAP and SHL verified, including top-bit overflow")


@cocotb.test()
async def test_fibonacci(dut):
    """The demonstration program: repeat {ADD; SWAP}. Checked against
    unbounded Python Fibonacci, not against the model's own output, so a
    shared bug cannot hide. Runs to F(100), past the F(93) point where 64-bit
    arithmetic overflows."""
    cocotb.start_soon(_clock(dut).start())
    await reset(dut)
    model = G.BigInt()

    await load_a(dut, model, 0)
    await load_b(dut, model, 1)

    fa, fb = 0, 1
    N = 100
    for k in range(1, N + 1):
        await issue(dut, G.OP_ADD)
        model.step(G.OP_ADD)
        await issue(dut, G.OP_SWAP)
        model.step(G.OP_SWAP)
        fa, fb = fb, fa + fb

        if k in (1, 2, 10, 50, 92, 93, 94, N):
            got = await read_a(dut, model)
            assert got == fa, f"F({k}): dut {got} expected {fa}"

    dut._log.info(f"Fibonacci verified to F({N}) = {fa}")
    dut._log.info("64-bit arithmetic overflows at F(93); this tile does not")


@cocotb.test()
async def test_software_multiply(dut):
    """Multiply implemented by the host out of SHL and ADD - the algorithm
    this tile deliberately does not put in hardware."""
    cocotb.start_soon(_clock(dut).start())
    rnd = random.Random(99)

    cases = [(0, 12345), (1, 1), (123456789, 987654321),
             ((1 << 32) - 1, (1 << 32) - 1),
             (rnd.getrandbits(40), rnd.getrandbits(40))]

    for a, b in cases:
        await reset(dut)
        model = G.BigInt()
        await load_a(dut, model, 0)
        await load_b(dut, model, a)

        hi = max(b.bit_length(), 1)
        for i in range(hi - 1, -1, -1):
            await issue(dut, G.OP_SHL)
            model.step(G.OP_SHL)
            if (b >> i) & 1:
                await issue(dut, G.OP_ADD)
                model.step(G.OP_ADD)

        got = await read_a(dut, model)
        assert got == (a * b) & G.MASK, f"{a} * {b}: dut {got} expected {a*b}"

    dut._log.info(f"{len(cases)} software multiplies verified against a*b")
