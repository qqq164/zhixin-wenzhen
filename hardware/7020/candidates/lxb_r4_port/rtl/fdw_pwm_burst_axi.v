`timescale 1ns / 1ps

// 有限周期 PWM 的独立 AXI4-Lite 控制，沿用已验证的地址／数据分开缓存原理。
// 完整参数快照启动，复位与取消停低；尚未接主工程或外部引脚。
module fdw_pwm_burst_axi (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aclk, ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET s_axi_aresetn" *)
    input wire s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aresetn, POLARITY ACTIVE_LOW" *)
    input wire s_axi_aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 12, READ_WRITE_MODE READ_WRITE" *)
    input wire [11:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *)
    input wire [2:0] s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input wire s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input wire s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output reg [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output reg s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input wire s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input wire [11:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *)
    input wire [2:0] s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input wire s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output reg [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output reg [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output reg s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input wire s_axi_rready,
    output wire pwm_out,
    output wire period_start,
    output wire burst_start,
    output wire done_event,
    output wire activity
);


    reg [11:0] write_address;
    reg [31:0] write_data;
    reg [3:0] write_strobes;
    reg address_pending, data_pending;
    reg [31:0] shadow_period, shadow_high, shadow_count;
    reg [31:0] launch_period, launch_high, launch_count;
    reg launch_valid, cancel_pulse;
    reg [3:0] error_sticky;
    reg done_sticky, cancelled_sticky;
    wire start_ready, cfg_error, running, done, cancelled;
    wire [31:0] active_period, active_high, active_count, completed_count;
    wire command_busy = launch_valid || !start_ready;
    assign activity = running || command_busy;
    wire launch_accepted = address_pending && data_pending && !s_axi_bvalid &&
        write_address == 12'h004 && write_strobes[0] && write_data[7:0] == 8'd1 &&
        shadow_period != 0 && shadow_high <= shadow_period && shadow_count != 0 && !command_busy;
    assign s_axi_awready = s_axi_aresetn && !address_pending && !s_axi_bvalid;
    assign s_axi_wready = s_axi_aresetn && !data_pending && !s_axi_bvalid;
    assign s_axi_arready = s_axi_aresetn && !s_axi_rvalid;
    assign burst_start = period_start && running && completed_count == 0;

    function [31:0] merge_bytes;
        input [31:0] original;
        input [31:0] incoming;
        input [3:0] strobes;
        integer index;
        begin
            merge_bytes = original;
            for (index = 0; index < 4; index = index + 1)
                if (strobes[index]) merge_bytes[index*8 +: 8] = incoming[index*8 +: 8];
        end
    endfunction

    fdw_pwm_burst core (
        .clk(s_axi_aclk), .resetn(s_axi_aresetn), .start_valid(launch_valid), .start_ready(start_ready),
        .cfg_period_ticks(launch_period), .cfg_high_ticks(launch_high), .cfg_period_count(launch_count),
        .cancel(cancel_pulse), .cfg_error(cfg_error), .running(running), .pwm_out(pwm_out),
        .period_start(period_start), .done(done), .done_event(done_event), .cancelled(cancelled),
        .active_period_ticks(active_period), .active_high_ticks(active_high),
        .active_period_count(active_count), .completed_periods(completed_count)
    );
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            write_address <= 0; write_data <= 0; write_strobes <= 0;
            address_pending <= 0; data_pending <= 0;
            s_axi_bvalid <= 0; s_axi_bresp <= 0;
            s_axi_rvalid <= 0; s_axi_rresp <= 0; s_axi_rdata <= 0;
            shadow_period <= 0; shadow_high <= 0; shadow_count <= 0;
            launch_period <= 0; launch_high <= 0; launch_count <= 0;
            launch_valid <= 0; cancel_pulse <= 0;
            error_sticky <= 0; done_sticky <= 0; cancelled_sticky <= 0;
        end else begin
            launch_valid <= 0;
            cancel_pulse <= 0;
            if (s_axi_awvalid && s_axi_awready) begin
                write_address <= s_axi_awaddr; address_pending <= 1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                write_data <= s_axi_wdata; write_strobes <= s_axi_wstrb; data_pending <= 1;
            end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
            // 写响应背压时不接收下一笔地址／数据，不重放已执行的命令。
            if (address_pending && data_pending && !s_axi_bvalid) begin
                address_pending <= 0; data_pending <= 0;
                s_axi_bvalid <= 1; s_axi_bresp <= 0;
                case (write_address)
                    12'h004: if (write_strobes[0]) begin
                        if (write_data[7:0] == 8'd1) begin
                            if (shadow_period == 0 || shadow_high > shadow_period || shadow_count == 0) begin
                                s_axi_bresp <= 2'b10; error_sticky[1] <= 1;
                            end else if (command_busy) begin
                                s_axi_bresp <= 2'b10; error_sticky[2] <= 1;
                            end else begin
                                launch_period <= shadow_period; launch_high <= shadow_high;
                                launch_count <= shadow_count; launch_valid <= 1;
                                done_sticky <= 0; cancelled_sticky <= 0;
                            end
                        end else if (write_data[7:0] == 8'd2) cancel_pulse <= 1;
                        else if (write_data[7:0] != 0) begin
                            s_axi_bresp <= 2'b10; error_sticky[0] <= 1;
                        end
                    end
                    12'h00C: begin
                        if (write_strobes[0]) error_sticky <= error_sticky & ~write_data[3:0];
                        if (write_strobes[1]) begin
                            if (write_data[8]) done_sticky <= 0;
                            if (write_data[9]) cancelled_sticky <= 0;
                        end
                    end
                    12'h010: shadow_period <= merge_bytes(shadow_period, write_data, write_strobes);
                    12'h014: shadow_high <= merge_bytes(shadow_high, write_data, write_strobes);
                    12'h018: shadow_count <= merge_bytes(shadow_count, write_data, write_strobes);
                    default: begin s_axi_bresp <= 2'b10; error_sticky[3] <= 1; end
                endcase
            end
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid <= 1; s_axi_rresp <= 0;
                case (s_axi_araddr)
                    12'h000: s_axi_rdata <= 32'h42555231;
                    12'h004: s_axi_rdata <= 0;
                    12'h008: s_axi_rdata <= {26'b0, (|error_sticky), cancelled_sticky, done_sticky,
                                            done, command_busy, running};
                    12'h00C: s_axi_rdata <= {22'b0, cancelled_sticky, done_sticky, 4'b0, error_sticky};
                    12'h010: s_axi_rdata <= shadow_period;
                    12'h014: s_axi_rdata <= shadow_high;
                    12'h018: s_axi_rdata <= shadow_count;
                    12'h020: s_axi_rdata <= active_period;
                    12'h024: s_axi_rdata <= active_high;
                    12'h028: s_axi_rdata <= active_count;
                    12'h02C: s_axi_rdata <= completed_count;
                    default: begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                endcase
            end
            // 同拍事件优先于清除，完成／取消脉冲保持为 CPU 可查询的状态。
            if (cfg_error) error_sticky[1] <= 1;
            if (done_event && !launch_accepted) done_sticky <= 1;
            if (cancelled && !launch_accepted) cancelled_sticky <= 1;
        end
    end
endmodule
