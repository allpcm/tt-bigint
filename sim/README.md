# Standalone bench

Not provided for this tile. The cocotb bench in `../test/` is authoritative and
is what Tiny Tapeout CI runs; a second Verilog bench was useful for the ternary
MLP tile mainly as a no-Python fallback and is not worth maintaining twice.

    cd ../test && make            # RTL
    cd ../test && make GATES=yes  # gate level, needs gate_level_netlist.v
