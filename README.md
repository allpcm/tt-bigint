![gds](../../workflows/gds/badge.svg) ![docs](../../workflows/docs/badge.svg) ![test](../../workflows/test/badge.svg)

# 128-bit Bit-Serial Integer Accumulator

Exact 128-bit integer arithmetic from **one full adder**. Two 128-bit registers
rotate past it; after 128 clocks the accumulator holds the sum.

Companion tile to [tt-ternary-mlp](https://github.com/allpcm/tt-ternary-mlp).
Those two chips hold a comparable number of flip-flops and answer "what should
transistors buy?" in opposite directions — 160 approximate weights evaluated in
parallel, or two exact integers evaluated one bit at a time.

**[Full datasheet and instruction set → `docs/info.md`](docs/info.md)**

---

## Why bit-serial

A 128-bit ripple-carry adder is roughly 148 levels of logic. At this process and
the slow corner that is on the order of 100 ns — five times the 20 ns clock
period. It cannot close at 50 MHz, and an adder wide enough to try would
dominate the tile.

The bit-serial adder is four levels deep. It costs 128 clocks per addition
instead of one. That trade is the design, and the measurements below say it was
the right one by a wider margin than expected.

Subtraction is the same loop with B inverted and the carry seeded to 1 — two's
complement, so there is no subtractor on the die at all.

## Verified results

CI run [`40520428533`](https://github.com/allpcm/tt-bigint/actions/runs/40520428533)
— LibreLane, sky130A. All four `gds` jobs green: build, precheck, gate-level
test, viewer.

| | |
|---|---|
| Tile size / clock | 2×2, 50 MHz |
| Synthesis | 1,577 cells, 17,600 µm² |
| Post-PnR standard cells | 3,221 instances, 25,092 µm² |
| Die area / utilization | 75,602 µm² / **34.6%** |
| Flip-flops | 277, 5,892 µm² |
| Worst setup slack | 4.34 ns of a 20 ns period (ss, 100 °C, 1.60 V) |
| Worst hold slack | +0.113 ns (ff, −40 °C, 1.95 V) |
| Setup/hold TNS and WNS | 0.0 at every corner |
| DRC (route and magic) | 0 |
| Tiny Tapeout precheck | pass |
| Verilator `-Wall`, inferred latches | clean, 0 |
| Testbench | 5 tests, RTL and gate-level, all passing |
| ADD / SUB latency | 130 cycles, fixed |

### Where the time actually goes

Static timing on the final netlist (OpenSTA, typical corner — LibreLane's
4.34 ns figure above is the slow corner, same paths derated):

| path | arrival | slack |
|---|---|---|
| worst register → register (the datapath) | 3.924 ns | 16.150 ns |
| zero flag → `uio_out[6]` | 3.734 ns | 12.016 ns |
| carry flag → `uio_out[7]` | 1.024 ns | 14.726 ns |
| worst overall: `uio_in[1]` → register | 8.250 ns | 11.798 ns |

The datapath is the *fastest* thing in the design. Breaking down that worst
path:

| | ns |
|---|---|
| SDC's assumed external input delay | 4.000 |
| actual combinational logic (5 cells) | 1.271 |
| fanout and hold-fix buffers (7 cells) | 2.977 |

**The design contains 1.27 ns of logic.** The rest is an assumption about when a
host presents data, plus buffer chains the flow inserted — the cell names say so
(`fanout28`, `fanout117`, `hold353`). The bit-serial adder is shallow enough that
LibreLane had to *add* delay to meet hold.

### One decision explains both costs

Eight opcodes over two 128-bit registers means every opcode bit fans out to 256
register write-mode multiplexers. That is where the area goes — generic
synthesis maps it to 1,687 NAND gates — and where the timing goes: 2.98 ns of
fanout buffering on the critical path. The arithmetic the tile is nominally
*for* is nearly free in both.

Worth knowing before anyone proposes a ninth opcode.

### Not quoted here

LibreLane reports `power__total` of 2.406 mW. That assumes a default switching
activity and is not meaningful for this design; an annotated figure from the
real vector set has not been measured yet.

## The demonstration: exact Fibonacci

Load A = 0, B = 1, then repeat `ADD` then `SWAP`. After n rounds A holds F(n).

64-bit arithmetic — every ordinary computer in the room — overflows at **F(93)**.
This tile is exact through **F(186)**:

```
332825110087067562321196029789634457848
```

Thirty-nine digits, the largest Fibonacci number below 2^128. The gate-level
bench verifies the fabricated netlist computes F(100) correctly.

## Running it locally

Needs [Icarus Verilog](https://steveicarus.github.io/iverilog/) and Python 3.11+.

```bash
python3 -m venv ~/.venvs/tt && source ~/.venvs/tt/bin/activate
cd test && pip install -r requirements.txt
make                      # RTL simulation
make GATES=yes            # gate level, needs gate_level_netlist.v from GDS.zip
```

Expect `TESTS=5 PASS=5 FAIL=0`. The suite covers byte I/O round-trips and that
`OUT` is non-destructive, add and subtract against random 128-bit operands with
the carry and borrow edge cases, swap and shift including the bit that falls off
the top, Fibonacci to F(100) checked against unbounded Python arithmetic rather
than against the model's own output, and a software multiply checked against
`a * b`.

`test/golden.py` is an independent reference in plain Python integers — runnable
on its own, with self-checks.

## Instruction set

Opcode on `uio[2:0]`, pulse `uio[3]` for one clock.

| op | name | effect | cycles |
|---|---|---|---|
| 0 | NOP | — | — |
| 1 | LDA | `A <= {A[119:0], ui_in}` | 2 |
| 2 | LDB | `B <= {B[119:0], ui_in}` | 2 |
| 3 | ADD | `A <= A + B` | 130 |
| 4 | SUB | `A <= A - B` | 130 |
| 5 | SWAP | `A <= B, B <= A` | 2 |
| 6 | SHL | `A <= A << 1` | 2 |
| 7 | OUT | emit `A[127:120]`, rotate A left 8 | 2 |

Status on `uio[7:4]`: busy, done, zero, carry (after `SUB`, carry means *no
borrow*).

**No hardware multiply, deliberately.** A host builds one from `SHL` and `ADD`
in six lines — Horner, MSB first — which puts the algorithm where a student can
read and change it. The bench verifies that path works.

## Using it from a microcontroller

The tile is a peripheral, not a system: no clock generator, no boot sequence. A
host drives `clk`, holds reset, loads operands a byte at a time, pulses an
opcode and reads a byte back. Because the host supplies the clock, the chip can
be single-stepped and watched on a logic analyzer — or an LED.

Budget about 17 I/O lines: `clk`, `rst_n`, 8 data in, 8 data out, 4 control, 4
status. The RTL also synthesizes unchanged for a Spartan-7, so the same host
code can drive an FPGA now and the fabricated tile later.

## Author

Phillip Moore — CTE Pre-Engineering, Foreman College and Career Academy, Chicago.

## About Tiny Tapeout

[Tiny Tapeout](https://tinytapeout.com) makes it practical to get a digital
design manufactured on a real chip. See the [FAQ](https://tinytapeout.com/faq/)
and the [digital design lessons](https://tinytapeout.com/digital_design/).

Licensed under Apache 2.0 — see [LICENSE](LICENSE).
