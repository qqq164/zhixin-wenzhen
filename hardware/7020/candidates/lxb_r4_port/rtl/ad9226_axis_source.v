`timescale 1ns / 1ps

// AD9226 采样数据到 AXI4-Stream 的帧封装模块。
// 控制字bit0启动一帧，bit1清理采集和FIFO；ADC时钟保持连续。
module ad9226_axis_source #(
    parameter integer FRAME_SAMPLES = 8192
)(
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME sample_clk, ASSOCIATED_BUSIF M_AXIS, ASSOCIATED_RESET aresetn" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 sample_clk CLK" *)
    input  wire        sample_clk,
    input  wire        adc_clk_0,
    input  wire        aresetn,

    input  wire [11:0] adc_data,
    input  wire        adc_otr,
    output wire        adc_clk,

    input  wire [1:0]  capture_control,
    output wire        capture_start_sampled,
    output wire        capture_clear_ack,
    output wire        fifo_aresetn,
    output reg         capture_active,
    output reg         capture_done,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *)
    output reg  [15:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output reg         m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input  wire        m_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output reg         m_axis_tlast
);

    localparam integer COUNT_WIDTH = $clog2(FRAME_SAMPLES + 1);

    reg [COUNT_WIDTH-1:0] sample_count;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg capture_start_meta;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg capture_start_sync;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg capture_clear_meta;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg capture_clear_sync;
    reg capture_start_delay;
    reg capture_discontinuous;

    wire capture_start_rise = capture_start_sync & ~capture_start_delay;
    assign capture_start_sampled = capture_start_sync;
    assign capture_clear_ack = capture_clear_sync;
    assign fifo_aresetn = aresetn & ~capture_clear_sync;

    // 用 ODDR 输出占空比稳定的 ADC 采样时钟。
    ODDR #(
        .DDR_CLK_EDGE("SAME_EDGE")
    ) adc_clk_oddr (
        .Q  (adc_clk),
        .C  (adc_clk_0),
        .CE (1'b1),
        .D1 (1'b1),
        .D2 (1'b0),
        .R  (~aresetn),
        .S  (1'b0)
    );

    // PS保持清理电平直到收到确认，两级同步器不受局部清理复位影响。
    always @(posedge sample_clk) begin
        if (!aresetn) begin
            capture_start_meta  <= 1'b0;
            capture_start_sync  <= 1'b0;
            capture_clear_meta  <= 1'b0;
            capture_clear_sync  <= 1'b0;
            capture_start_delay <= 1'b0;
        end else begin
            capture_start_meta  <= capture_control[0];
            capture_start_sync  <= capture_start_meta;
            capture_clear_meta  <= capture_control[1];
            capture_clear_sync  <= capture_clear_meta;
            capture_start_delay <= capture_start_sync;
        end
    end

    // bit15=OTR，bit14=采样不连续，bit13=新版格式标记，bit12=0，bit11:0=原始码。
    always @(posedge sample_clk) begin
        if (!aresetn || capture_clear_sync) begin
            sample_count   <= {COUNT_WIDTH{1'b0}};
            capture_active <= 1'b0;
            capture_done   <= 1'b0;
            m_axis_tdata   <= 16'd0;
            m_axis_tvalid  <= 1'b0;
            m_axis_tlast   <= 1'b0;
            capture_discontinuous <= 1'b0;
        end else begin
            // 只在仍需采样时记录停顿；仅等待最后一个已采样数据不算丢点。
            if (capture_active && m_axis_tvalid && !m_axis_tready &&
                sample_count < FRAME_SAMPLES)
                capture_discontinuous <= 1'b1;
            if (capture_start_rise && !capture_active) begin
                sample_count   <= {COUNT_WIDTH{1'b0}};
                capture_active <= 1'b1;
                capture_done   <= 1'b0;
                m_axis_tvalid  <= 1'b0;
                m_axis_tlast   <= 1'b0;
                capture_discontinuous <= 1'b0;
            end else if (capture_active && (!m_axis_tvalid || m_axis_tready)) begin
                if (sample_count < FRAME_SAMPLES) begin
                    m_axis_tdata  <= {adc_otr, capture_discontinuous, 1'b1, 1'b0, adc_data};
                    m_axis_tvalid <= 1'b1;
                    m_axis_tlast  <= (sample_count == FRAME_SAMPLES - 1);
                    sample_count  <= sample_count + 1'b1;
                end else begin
                    // 最后一个数据已经被下游接收，一帧采集完成。
                    capture_active <= 1'b0;
                    capture_done   <= 1'b1;
                    m_axis_tvalid  <= 1'b0;
                    m_axis_tlast   <= 1'b0;
                end
            end
        end
    end

endmodule
