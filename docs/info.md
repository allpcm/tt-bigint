# 128-bit Bit-Serial Integer Accumulator

## How it works

This tile does exact 128-bit integer arithmetic with **one full adder**.

Two 128-bit registers, A and B, hold the operands. To add them, both registers
rotate right together. Each clock the full adder consumes A[0] and B[0] along
with a stored carry bit, and pushes the sum bit into the top of A. After 128
clocks A holds the sum — and B is back exactly where it started, because a
128-bit register rotated 128 times is unchanged.

Subtraction is the same loop with B inverted and the carry seeded to 1. That is
two's complement, and it means the tile needs no subtractor at all.

### Why bit-serial

A 128-bit ripple-carry adder is roughly 148 levels of logic deep. At this
process and the slow corner that is on the order of 100 ns — five times the
20 ns clock period. It cannot close timing at 50 MHz, and a parallel adder
wide enough to try would dominate the tile.

The bit-serial adder is four levels deep and closes with room to spare. It
costs 128 clocks per addition instead of one. **That trade is the design.**

It is also how the first generation of computers worked, for the same reason:
when arithmetic hardware is the scarce resource, you spend time to save it.

### Companion to the ternary MLP tile

These two tiles hold almost the same number of flip-flops and answer the
question "what should transistors buy?" in opposite directions.

| | ternary MLP tile | this tile |
|---|---|---|
| state | 160 weights, 2 bits each | 2 integers, 128 bits each |
| arithmetic | 8 accumulators in parallel | 1 full adder, serial |
| precision | approximate, ±1 weights | exact |
| latency | 26 cycles, fixed | 130 cycles per add |
| answers | "which of four patterns is this?" | "what is the exact value?" |

Neither is better. Which one is right depends entirely on the question being
asked, and that is the point of building both.

## Measured results

CI run 40520428533, LibreLane, sky130A. All checks green.

| | |
|---|---|
| Synthesis | 1,577 cells, 17,600 µm² |
| Post-PnR standard cells | 3,221 instances, 25,092 µm² |
| Die area (2×2) / utilization | 75,602 µm² / 34.6% |
| Flip-flops | 277, 5,892 µm² |
| Worst setup slack | 4.34 ns of 20 ns (ss, 100 °C, 1.60 V) |
| Worst hold slack | +0.113 ns (ff, −40 °C, 1.95 V) |
| Setup/hold TNS, WNS | 0.0 at every corner |
| DRC route / magic | 0 / 0 |
| Verilator `-Wall`, inferred latches | clean, 0 |

Static timing at the typical corner puts the worst register-to-register path —
the actual datapath — at 3.924 ns arrival with 16.15 ns of slack, the fastest
class of path in the design. The overall worst path runs from an opcode input
pin to a register at 8.250 ns, of which 4.000 ns is the constraint file's
assumed external input delay and 2.977 ns is fanout and hold-fix buffering
inserted by the flow. **The design contains 1.27 ns of combinational logic.**

The bit-serial adder is shallow enough that the tool had to insert delay cells
to meet hold timing.

Both the area and the timing cost trace to the same decision: eight opcodes
across two 128-bit registers means every opcode bit drives 256 write-mode
multiplexers. The arithmetic is nearly free; selecting what to write is not.

Power has not been measured under annotated activity. LibreLane reports 2.406 mW
assuming default switching, which is not meaningful here and is not quoted.

## Instruction set

Opcode on `uio[2:0]`, pulse `uio[3]` high for one clock to execute.

| op | name | effect | cycles |
|---|---|---|---|
| 0 | NOP | — | — |
| 1 | LDA | `A <= {A[119:0], ui_in}` | 2 |
| 2 | LDB | `B <= {B[119:0], ui_in}` | 2 |
| 3 | ADD | `A <= A + B` | 130 |
| 4 | SUB | `A <= A - B` | 130 |
| 5 | SWAP | `A <= B, B <= A` | 2 |
| 6 | SHL | `A <= A << 1` (also "double") | 2 |
| 7 | OUT | emit `A[127:120]`, rotate A left 8 | 2 |

Sixteen `LDA` operations load a full 128-bit value, most significant byte
first. Sixteen `OUT` operations read it back in the same order and leave A
unchanged, because 16 rotations of one byte is a full 128-bit rotation.

Status appears on `uio[7:4]`: busy, done, zero (`A == 0`), and carry — which
after `SUB` means *no borrow*.

**There is no hardware multiply, on purpose.** A host builds one from `SHL`
and `ADD` in six lines, walking the multiplier MSB-first and doubling the
accumulator at each step. That puts the algorithm somewhere a student can read
and change it rather than burying it in the netlist. There is no right shift
either; `SHL` earns the last opcode slot because it is what multiplication
needs, and division is out of scope.

## The demonstration: exact Fibonacci

Load A = 0 and B = 1, then repeat `ADD` followed by `SWAP`. After n rounds A
holds F(n).

64-bit arithmetic — every ordinary computer in the room — overflows at
**F(93)**. This tile is exact through **F(186)**:

```
332825110087067562321196029789634457848
```

Thirty-nine digits, the largest Fibonacci number below 2^128.

## How to test

1. Hold `rst_n` low for several clocks, then release.
2. Load a value: for each of 16 bytes, most significant first, put the byte on
   `ui[7:0]`, opcode 1 on `uio[2:0]`, pulse `uio[3]` high for one clock, and
   wait for `done` on `uio[5]`.
3. Load the second operand the same way with opcode 2.
4. Pulse opcode 3. Wait 130 clocks; `done` rises.
5. Read the result: pulse opcode 7 sixteen times, taking a byte from
   `uo[7:0]` after each `done`. The value arrives most significant byte first
   and A is left unchanged.

`test/golden.py` is an independent reference in plain Python integers. The
cocotb bench checks round-trip I/O, add and subtract against random operands
and the carry and borrow edge cases, swap and shift, Fibonacci to F(100)
against unbounded Python arithmetic, and a software multiply against `a * b`.

## External hardware

None required. An Arduino, Pico, or the RP2040 on the Tiny Tapeout demo board
can drive the whole interface: 8 data in, 8 data out, 4 control out, 4 status
in. The host supplies the clock, so the tile can be single-stepped and watched.
