`timescale 1ns / 1ps

// PWM 的 AXI4-Lite 控制接口。地址与数据通道分别缓存，只允许一笔待应答事务。
// 周期和高电平先写影子寄存器，再通过控制寄存器一次性提交。
module fdw_pwm_axi (
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
    output wire activity
);

    reg [11:0] write_address;
    reg [31:0] write_data;
    reg [3:0] write_strobes;
    reg address_pending;
    reg data_pending;
    reg [31:0] shadow_period;
    reg [31:0] shadow_high;
    reg [31:0] commit_period;
    reg [31:0] commit_high;
    reg pwm_enable;
    reg commit_valid;
    reg error_sticky;
    reg applied_sticky;
    reg [31:0] cycle_count;
    wire cfg_ready, cfg_error, cfg_applied, running;
    wire [31:0] active_period, active_high;
    wire commit_busy = !cfg_ready || commit_valid;
    assign activity = pwm_enable || running || commit_busy;

    assign s_axi_awready = s_axi_aresetn && !address_pending && !s_axi_bvalid;
    assign s_axi_wready  = s_axi_aresetn && !data_pending && !s_axi_bvalid;
    assign s_axi_arready = s_axi_aresetn && !s_axi_rvalid;

    function [31:0] merge_bytes;
        input [31:0] original;
        input [31:0] incoming;
        input [3:0] strobes;
        integer byte_index;
        begin
            merge_bytes = original;
            for (byte_index = 0; byte_index < 4; byte_index = byte_index + 1)
                if (strobes[byte_index])
                    merge_bytes[byte_index*8 +: 8] = incoming[byte_index*8 +: 8];
        end
    endfunction

    fdw_pwm_core pwm_core (
        .clk(s_axi_aclk), .resetn(s_axi_aresetn), .enable(pwm_enable),
        .cfg_valid(commit_valid), .cfg_ready(cfg_ready),
        .cfg_period_ticks(commit_period), .cfg_high_ticks(commit_high),
        .cfg_error(cfg_error), .cfg_applied(cfg_applied),
        .pwm_out(pwm_out), .period_start(period_start), .running(running),
        .active_period_ticks(active_period), .active_high_ticks(active_high)
    );

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            write_address <= 0;
            write_data <= 0;
            write_strobes <= 0;
            address_pending <= 0;
            data_pending <= 0;
            s_axi_bvalid <= 0;
            s_axi_bresp <= 0;
            s_axi_rvalid <= 0;
            s_axi_rresp <= 0;
            s_axi_rdata <= 0;
            shadow_period <= 0;
            shadow_high <= 0;
            commit_period <= 0;
            commit_high <= 0;
            pwm_enable <= 0;
            commit_valid <= 0;
            error_sticky <= 0;
            applied_sticky <= 0;
            cycle_count <= 0;
        end else begin
            commit_valid <= 0;

            if (s_axi_awvalid && s_axi_awready) begin
                write_address <= s_axi_awaddr;
                address_pending <= 1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                write_data <= s_axi_wdata;
                write_strobes <= s_axi_wstrb;
                data_pending <= 1;
            end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;

            // 只有地址和数据都接收完毕，才执行一次寄存器写入。
            if (address_pending && data_pending && !s_axi_bvalid) begin
                address_pending <= 0;
                data_pending <= 0;
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                case (write_address)
                    12'h000: begin
                        if (write_strobes[0] && write_data[1] &&
                            (commit_busy || shadow_period == 0 || shadow_high > shadow_period)) begin
                            s_axi_bresp <= 2'b10;
                            error_sticky <= 1;
                        end else begin
                            if (write_strobes[0]) begin
                                pwm_enable <= write_data[0];
                                if (write_data[1]) begin
                                    commit_period <= shadow_period;
                                    commit_high <= shadow_high;
                                    commit_valid <= 1;
                                end
                            end
                            if (write_strobes[1] && write_data[8]) begin
                                error_sticky <= 0;
                                applied_sticky <= 0;
                            end
                        end
                    end
                    12'h004: shadow_period <= merge_bytes(shadow_period, write_data, write_strobes);
                    12'h008: shadow_high <= merge_bytes(shadow_high, write_data, write_strobes);
                    default: s_axi_bresp <= 2'b10;
                endcase
            end

            // 读应答在背压期间保持数据和响应码稳定。
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid <= 1;
                s_axi_rresp <= 2'b00;
                case (s_axi_araddr)
                    12'h000: s_axi_rdata <= {31'b0, pwm_enable};
                    12'h004: s_axi_rdata <= shadow_period;
                    12'h008: s_axi_rdata <= shadow_high;
                    12'h00C: s_axi_rdata <= {28'b0, applied_sticky, error_sticky, commit_busy, running};
                    12'h010: s_axi_rdata <= active_period;
                    12'h014: s_axi_rdata <= active_high;
                    12'h018: s_axi_rdata <= cycle_count;
                    12'h01C: s_axi_rdata <= 32'h50574D31;
                    default: begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                endcase
            end

            // 同周期新产生的事件优先于清除，避免丢失状态。
            if (cfg_error) error_sticky <= 1;
            if (cfg_applied) applied_sticky <= 1;
            if (period_start) cycle_count <= cycle_count + 1'b1;
        end
    end
endmodule
