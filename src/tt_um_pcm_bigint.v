`default_nettype none

// =============================================================================
// tt_um_pcm_bigint - a 128-bit bit-serial integer accumulator
//
// Companion tile to tt_um_pcm_ternary_mlp. That tile spends its flip-flops on
// 160 approximate weights evaluated in parallel; this one spends roughly the
// same number on two exact 128-bit integers evaluated one bit at a time. Same
// silicon budget, opposite answer to "what should transistors buy?".
//
// WHY BIT-SERIAL. A 128-bit ripple-carry add is roughly 148 levels of logic.
// At this process and corner that is on the order of 100 ns, five times the
// 20 ns clock period - it cannot close at 50 MHz. A bit-serial adder is one
// full adder and one carry flip-flop, four levels deep, and closes with room
// to spare. It costs 128 cycles per add instead of one. That trade is the
// whole design.
//
// HOW THE ADD WORKS. A and B rotate right together. Each cycle the full adder
// consumes A[0] and B[0] with the stored carry and pushes the sum bit into
// A[127]. After 128 cycles A holds the sum and B is back where it started,
// because a 128-bit register rotated 128 times is unchanged. Subtraction is
// the same loop with B inverted and the carry seeded to 1 - two's complement,
// no second adder needed.
//
// INTERFACE
//   ui_in [7:0]   data byte in
//   uo_out[7:0]   data byte out
//   uio_in[2:0]   opcode        uio_out[4] busy
//   uio_in[3]     strobe        uio_out[5] done
//                               uio_out[6] zero  (A == 0)
//                               uio_out[7] carry (carry-out of the last ADD,
//                                                 or NOT borrow after a SUB)
//
// OPCODES
//   0 NOP
//   1 LDA   A <= {A[119:0], ui_in}      shift a byte into A, MSB-first
//   2 LDB   B <= {B[119:0], ui_in}
//   3 ADD   A <= A + B                  128 cycles
//   4 SUB   A <= A - B                  128 cycles
//   5 SWAP  A <= B, B <= A
//   6 SHL   A <= A << 1                 also "double"
//   7 OUT   uo_out <= A[127:120], A rotates left 8 - sixteen OUTs read the
//           whole accumulator MSB-first and leave A unchanged
//
// There is deliberately no hardware multiply. A host implements it in six
// lines with SHL and ADD (Horner, MSB-first), which puts the algorithm where
// a student can read and modify it instead of burying it in the netlist.
// There is also no right shift; SHL earns its opcode slot by enabling that
// multiply, and division is out of scope.
//
// FIBONACCI, the demonstration: load A=0, B=1, then repeat {ADD; SWAP}. After
// n rounds A holds F(n). 64-bit arithmetic dies at F(93); this is exact to
// F(186), the largest Fibonacci number below 2^128.
// =============================================================================

module tt_um_pcm_bigint (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

    localparam W = 128;

    localparam OP_NOP  = 3'd0, OP_LDA  = 3'd1, OP_LDB = 3'd2, OP_ADD = 3'd3,
               OP_SUB  = 3'd4, OP_SWAP = 3'd5, OP_SHL = 3'd6, OP_OUT = 3'd7;

    localparam S_IDLE = 2'd0, S_RUN = 2'd1, S_DONE = 2'd2;

    // Terminal count for `cnt`, written as a sized literal so the comparison
    // below is 7 bits on both sides. An unsized (W-1) is a 32-bit expression
    // and trips WIDTHEXPAND; assigning it to a [6:0] localparam trips
    // WIDTHTRUNC instead. INVARIANT: LAST_BIT == W-1 and must fit in 7 bits.
    // Widening W past 128 means widening `cnt` first, or the counter wraps and
    // the FSM never leaves S_RUN. The bench catches that: latency stops being
    // exactly 131 cycles.
    localparam [6:0] LAST_BIT = 7'd127;

    wire [2:0] opcode = uio_in[2:0];
    wire       strobe = uio_in[3];

    reg [W-1:0] A, B;
    reg [2:0]   op;
    reg [1:0]   state;
    reg [6:0]   cnt;
    reg         carry;
    reg [7:0]   dout;

    // One full adder. B is inverted for subtraction; the carry was seeded to 1
    // when the op was latched, which completes the two's complement.
    wire       b_bit = B[0] ^ (op == OP_SUB);
    wire [1:0] fa    = A[0] + b_bit + carry;

    always @(posedge clk) begin
        if (!rst_n) begin
            A     <= {W{1'b0}};
            B     <= {W{1'b0}};
            op    <= OP_NOP;
            state <= S_IDLE;
            cnt   <= 7'd0;
            carry <= 1'b0;
            dout  <= 8'h00;
        end else begin
            case (state)

            S_IDLE: begin
                if (strobe && opcode != OP_NOP) begin
                    op  <= opcode;
                    cnt <= 7'd0;
                    case (opcode)
                        OP_ADD:  begin carry <= 1'b0; state <= S_RUN; end
                        OP_SUB:  begin carry <= 1'b1; state <= S_RUN; end
                        OP_LDA:  begin A <= {A[W-9:0], ui_in};        state <= S_DONE; end
                        OP_LDB:  begin B <= {B[W-9:0], ui_in};        state <= S_DONE; end
                        OP_SWAP: begin A <= B; B <= A;                state <= S_DONE; end
                        OP_SHL:  begin A <= {A[W-2:0], 1'b0};         state <= S_DONE; end
                        OP_OUT:  begin dout <= A[W-1:W-8];
                                       A <= {A[W-9:0], A[W-1:W-8]};
                                       state <= S_DONE; end
                        default: state <= S_DONE;
                    endcase
                end
            end

            // Bit-serial add/sub. Both registers rotate right; the sum bit
            // enters A at the top. 128 rotations return B to its start.
            S_RUN: begin
                A     <= {fa[0], A[W-1:1]};
                B     <= {B[0],  B[W-1:1]};
                carry <= fa[1];
                if (cnt == LAST_BIT) state <= S_DONE;
                else                 cnt   <= cnt + 1'b1;
            end

            S_DONE:  state <= S_IDLE;
            default: state <= S_IDLE;
            endcase
        end
    end

    // 128-bit NOR. Deep but reg-to-pin with a full cycle available, and it
    // keeps the flag honest after every op rather than only after arithmetic.
    wire zero = ~(|A);

    assign uo_out  = dout;
    assign uio_out = {carry, zero, (state == S_DONE), (state != S_IDLE), 4'b0000};
    assign uio_oe  = 8'b1111_0000;

    // ena is driven high by the harness whenever this tile is selected.
    wire _unused = &{ena, uio_in[7:4], 1'b0};

endmodule

`default_nettype wire
