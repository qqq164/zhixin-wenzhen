`timescale 1ns / 1ps

// 两路探针各自可输入或输出 PWM；复位与未许可时均保持高阻。
module fdw_lxb_probe_io (
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 resetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME resetn, POLARITY ACTIVE_LOW" *)
    input wire resetn,
    input wire permit,
    input wire [1:0] output_mask,
    input wire pwm_a,
    input wire pwm_b,
    output wire [1:0] digital_in,
    inout wire [1:0] probe_io
);
    IOBUF probe_a (.I(pwm_a), .O(digital_in[0]),
        .T(!(resetn && permit && output_mask[0])), .IO(probe_io[0]));
    IOBUF probe_b (.I(pwm_b), .O(digital_in[1]),
        .T(!(resetn && permit && output_mask[1])), .IO(probe_io[1]));
endmodule
