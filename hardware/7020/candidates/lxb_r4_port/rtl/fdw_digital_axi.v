`timescale 1ns / 1ps

// 数字记录器的 AXI4-Lite 控制与同步读回。输入必须已同步到总线时钟。
// 不接外部管脚，不设置默认速率；影子参数经一次装载形成完整快照。
module fdw_digital_axi #(
    parameter integer SAMPLE_WIDTH = 2,
    parameter integer MAX_SAMPLES = 1024
) (
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
    input wire [SAMPLE_WIDTH-1:0] sample_in,
    input wire trigger_in,
    input wire invalidate_in,
    output wire capture_active
);
    reg [11:0] write_address;
    reg [31:0] write_data;
    reg [3:0] write_strobes;
    reg address_pending, data_pending;
    reg [31:0] shadow_interval, shadow_count;
    reg shadow_wait;
    reg [31:0] arm_interval, arm_count;
    reg arm_wait, arm_valid;
    reg cancel_pulse, soft_trigger;
    reg error_sticky, cancel_sticky, trigger_sticky;
    reg [31:0] read_index, memory_index;
    reg memory_request, memory_pending;
    wire memory_valid, memory_error;
    wire [SAMPLE_WIDTH-1:0] memory_data;
    wire arm_ready, cfg_error, armed, busy, done, record_valid, cancelled, trigger_seen;
    wire [31:0] captured_count, active_interval, active_count;
    assign capture_active = armed || busy || arm_valid || memory_pending || s_axi_rvalid;

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

    wire [31:0] control_command = merge_bytes(32'b0, write_data, write_strobes);
    wire conflicting_commands = (control_command[0] && control_command[1]) ||
                                (control_command[0] && control_command[2]) ||
                                (control_command[1] && control_command[2]);
    wire invalid_arm = invalidate_in || !arm_ready || arm_valid || shadow_interval == 0 ||
                       shadow_count == 0 || shadow_count > MAX_SAMPLES;

    assign s_axi_awready = s_axi_aresetn && !address_pending && !s_axi_bvalid;
    assign s_axi_wready = s_axi_aresetn && !data_pending && !s_axi_bvalid;
    assign s_axi_arready = s_axi_aresetn && !s_axi_rvalid && !memory_pending;

    fdw_digital_capture #(.SAMPLE_WIDTH(SAMPLE_WIDTH), .MAX_SAMPLES(MAX_SAMPLES)) capture (
        .clk(s_axi_aclk), .resetn(s_axi_aresetn && !invalidate_in), .sample_in(sample_in),
        .arm_valid(arm_valid), .arm_ready(arm_ready),
        .cfg_interval_ticks(arm_interval), .cfg_sample_count(arm_count),
        .cfg_wait_trigger(arm_wait), .trigger(trigger_in || soft_trigger),
        .cancel(cancel_pulse), .cfg_error(cfg_error), .armed(armed), .busy(busy),
        .done(done), .record_valid(record_valid), .cancelled(cancelled),
        .trigger_seen(trigger_seen), .captured_count(captured_count),
        .active_interval_ticks(active_interval), .active_sample_count(active_count),
        .rd_request(memory_request), .rd_index(memory_index),
        .rd_valid(memory_valid), .rd_error(memory_error), .rd_data(memory_data)
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
            s_axi_rdata <= 0;
            s_axi_rresp <= 0;
            shadow_interval <= 0;
            shadow_count <= 0;
            shadow_wait <= 0;
            arm_interval <= 0;
            arm_count <= 0;
            arm_wait <= 0;
            arm_valid <= 0;
            cancel_pulse <= 0;
            soft_trigger <= 0;
            error_sticky <= 0;
            cancel_sticky <= 0;
            trigger_sticky <= 0;
            read_index <= 0;
            memory_index <= 0;
            memory_request <= 0;
            memory_pending <= 0;
        end else begin
            if (arm_valid && arm_ready) arm_valid <= 0;
            cancel_pulse <= 0;
            soft_trigger <= 0;
            memory_request <= 0;
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

            if (address_pending && data_pending && !s_axi_bvalid) begin
                address_pending <= 0;
                data_pending <= 0;
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                case (write_address)
                    12'h000: begin
                        if ((control_command & 32'hfffffef8) != 0 || conflicting_commands ||
                            (control_command[0] && invalid_arm)) begin
                            s_axi_bresp <= 2'b10;
                            error_sticky <= 1;
                        end else begin
                            if (control_command[8]) begin
                                error_sticky <= 0;
                                cancel_sticky <= 0;
                                trigger_sticky <= 0;
                            end
                            if (control_command[0]) begin
                                arm_interval <= shadow_interval;
                                arm_count <= shadow_count;
                                arm_wait <= shadow_wait;
                                arm_valid <= 1;
                            end
                            if (control_command[1]) begin
                                arm_valid <= 0;
                                cancel_pulse <= 1;
                            end
                            if (control_command[2]) soft_trigger <= 1;
                        end
                    end
                    12'h004: shadow_interval <= merge_bytes(shadow_interval, write_data, write_strobes);
                    12'h008: shadow_count <= merge_bytes(shadow_count, write_data, write_strobes);
                    12'h00c: begin
                        if ((control_command & 32'hfffffffe) != 0) s_axi_bresp <= 2'b10;
                        else if (write_strobes[0]) shadow_wait <= write_data[0];
                    end
                    12'h02c: read_index <= merge_bytes(read_index, write_data, write_strobes);
                    default: s_axi_bresp <= 2'b10;
                endcase
            end

            // RAM 读应答经独立等待阶段到达，期间不接受下一读地址。
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
            if (memory_pending && memory_valid) begin
                memory_pending <= 0;
                s_axi_rvalid <= 1;
                s_axi_rdata <= {{(32-SAMPLE_WIDTH){1'b0}}, memory_data};
                s_axi_rresp <= memory_error ? 2'b10 : 2'b00;
            end
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid <= 1;
                s_axi_rresp <= 2'b00;
                case (s_axi_araddr)
                    12'h000: s_axi_rdata <= 0;
                    12'h004: s_axi_rdata <= shadow_interval;
                    12'h008: s_axi_rdata <= shadow_count;
                    12'h00c: s_axi_rdata <= {31'b0, shadow_wait};
                    12'h010: s_axi_rdata <= {24'b0, arm_valid, trigger_sticky, cancel_sticky,
                                            error_sticky, record_valid, done, busy, armed};
                    12'h014: s_axi_rdata <= captured_count;
                    12'h018: s_axi_rdata <= active_interval;
                    12'h01c: s_axi_rdata <= active_count;
                    // DIG5 对应ADC、内部DDS/DAC902及双IO整合配套。
                    12'h020: s_axi_rdata <= 32'h44494735;
                    12'h024: s_axi_rdata <= MAX_SAMPLES;
                    12'h028: s_axi_rdata <= SAMPLE_WIDTH;
                    12'h02c: s_axi_rdata <= read_index;
                    12'h030: begin
                        s_axi_rvalid <= 0;
                        memory_index <= read_index;
                        memory_request <= 1;
                        memory_pending <= 1;
                    end
                    default: begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                endcase
            end
            // 新事件优先于软件清除，保证同时发生的事件仍可观察。
            if (cfg_error) error_sticky <= 1;
            if (cancelled) cancel_sticky <= 1;
            if (trigger_seen) trigger_sticky <= 1;
            // 模式切换使完整旧帧和在途装载失效，但不复位 AXI 握手。
            // 存储读若被中止，给出明确错误；已发布且背压的响应保持原值。
            if (invalidate_in) begin
                arm_valid <= 0;
                cancel_pulse <= 0;
                soft_trigger <= 0;
                error_sticky <= 0;
                cancel_sticky <= 0;
                trigger_sticky <= 0;
                memory_request <= 0;
                if (memory_pending || (s_axi_arvalid && s_axi_arready && s_axi_araddr == 12'h030)) begin
                    memory_pending <= 0;
                    s_axi_rvalid <= 1;
                    s_axi_rdata <= 0;
                    s_axi_rresp <= 2'b10;
                end
            end
        end
    end
endmodule
