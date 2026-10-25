`timescale 1ns / 1ps

// R4：原速率逐点路径保持，低频分支在原时钟域低通抽取。
// 控制字bit0启动一帧，bit1清理采集和FIFO；ADC时钟保持连续。
module fdw_adc_phase_axis_source #(
    parameter integer FRAME_SAMPLES = 8192
)(
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME sample_clk, ASSOCIATED_BUSIF M_AXIS, ASSOCIATED_RESET aresetn" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 sample_clk CLK" *)
    input  wire        sample_clk,
    input  wire        adc_clk_0,
    input wire control_clk,
    input wire control_resetn,
    input wire [31:0] phase_command_word,
    input wire [6:0] legacy_status,
    output wire [31:0] phase_status_word,
    input wire reference_valid,
    input wire reference_inverted,
    input wire [31:0] reference_phase,
    input wire [31:0] reference_ftw,
    input wire [31:0] reference_tag,
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
    reg [4:0] captured_log2;
    wire configure, filter_error, filtered_valid, filtered_overrange, filtered_reference_valid;
    wire [4:0] config_log2, active_log2;
    wire signed [11:0] filtered_code;
    wire [31:0] filtered_phase, filtered_tag;
    wire sample_valid = active_log2==0 || filtered_valid;
    wire [11:0] selected_code = active_log2==0 ? adc_data : {~filtered_code[11],filtered_code[10:0]};
    wire selected_overrange = active_log2==0 ? adc_otr : filtered_overrange;
    wire [31:0] selected_phase = active_log2==0 ? reference_phase : filtered_phase;
    wire [19:0] selected_interval = (20'd1 << active_log2)-1'b1;
    wire sample_commit = capture_active&&(!m_axis_tvalid||m_axis_tready)&&
                         sample_count<FRAME_SAMPLES&&sample_valid;
    // 只给低频分支增加入口寄存，避免ADC外部半周期预算承受整条滤波组合链。
    // 原码、过量程、DDS参考及配置身份同拍延迟；直通分支仍使用原信号。
    reg signed [11:0] filter_input_code;
    reg filter_input_overrange,filter_input_valid;
    reg [31:0] filter_input_phase,filter_input_tag;
    always @(posedge sample_clk) begin
        if(!aresetn)begin
            filter_input_code<=0;filter_input_overrange<=0;filter_input_valid<=0;
            filter_input_phase<=0;filter_input_tag<=0;
        end else begin
            filter_input_code<={~adc_data[11],adc_data[10:0]};
            filter_input_overrange<=adc_otr;filter_input_valid<=reference_valid;
            filter_input_phase<=reference_phase;filter_input_tag<=reference_tag;
        end
    end
    fdw_cic3_decimator #(.MAX_LOG2(16)) lowrate_filter(
        .sample_clk(sample_clk),.aresetn(aresetn),.clear(capture_clear_sync),
        .configure(configure),.config_log2(config_log2),
        .input_code(filter_input_code),.input_overrange(filter_input_overrange),
        .input_phase(filter_input_phase),.input_tag(filter_input_tag),.input_reference_valid(filter_input_valid),
        .config_error(filter_error),.active_log2(active_log2),.output_code(filtered_code),
        .output_overrange(filtered_overrange),.output_valid(filtered_valid),
        .output_phase(filtered_phase),.output_tag(filtered_tag),.output_reference_valid(filtered_reference_valid)
    );

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
            captured_log2 <= 0;
        end else begin
            if(m_axis_tvalid&&m_axis_tready)begin m_axis_tvalid<=0;m_axis_tlast<=0;end
            // 只在仍需采样时记录停顿；仅等待最后一个已采样数据不算丢点。
            if (capture_active && m_axis_tvalid && !m_axis_tready &&
                sample_count < FRAME_SAMPLES && sample_valid)
                capture_discontinuous <= 1'b1;
            if (capture_start_rise && !capture_active) begin
                sample_count   <= {COUNT_WIDTH{1'b0}};
                capture_active <= 1'b1;
                capture_done   <= 1'b0;
                m_axis_tvalid  <= 1'b0;
                m_axis_tlast   <= 1'b0;
                capture_discontinuous <= 1'b0;
                captured_log2 <= active_log2;
            end else if (capture_active && (!m_axis_tvalid || m_axis_tready)) begin
                if (sample_count < FRAME_SAMPLES) begin
                    if(sample_valid)begin
                        m_axis_tdata  <= {selected_overrange, capture_discontinuous, 1'b1, 1'b0, selected_code};
                        m_axis_tvalid <= 1'b1;
                        m_axis_tlast  <= (sample_count == FRAME_SAMPLES - 1);
                        sample_count  <= sample_count + 1'b1;
                    end
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

    // 只观察实际采入样本的时刻；不改变原ADC接收、FIFO或AXIS状态机。
    wire phase_active,phase_done,phase_inverted;
    wire [7:0] phase_errors;
    wire [31:0] phase_generation,phase_count,first_phase,last_phase,frame_ftw,frame_tag;
    wire [19:0] phase_interval;
    wire [63:0] first_tick,last_tick;
    reg [511:0] metadata_words;
    fdw_phase_reference #(.FRAME_SAMPLES(FRAME_SAMPLES)) phase_record(
        .sample_clk(sample_clk),.aresetn(aresetn),.clear(capture_clear_sync),
        .arm(capture_start_rise&&!capture_active),
        .sample_commit(sample_commit),
        .frame_complete(capture_active&&(!m_axis_tvalid||m_axis_tready)&&sample_count==FRAME_SAMPLES),
        .stream_discontinuous(capture_discontinuous||filter_error),
        .reference_valid(reference_valid&&(!sample_commit||active_log2==0||
                          (filtered_reference_valid&&filtered_tag==reference_tag))),
        .reference_inverted(reference_inverted),.reference_phase(selected_phase),
        .reference_ftw(reference_ftw),.reference_tag(reference_tag),.interval_minus_one(selected_interval),
        .active(phase_active),.done(phase_done),.errors(phase_errors),.generation(phase_generation),
        .accepted_count(phase_count),.first_phase(first_phase),.last_phase(last_phase),
        .frame_ftw(frame_ftw),.frame_tag(frame_tag),.frame_interval_minus_one(phase_interval),
        .frame_inverted(phase_inverted),.first_tick(first_tick),.last_tick(last_tick)
    );
    always @*begin
        metadata_words=0;
        metadata_words[0*16+:16]=16'hD907;
        metadata_words[1*16+:16]={6'b0,phase_errors,phase_done,phase_active};
        metadata_words[2*16+:32]=phase_generation;
        metadata_words[4*16+:32]=phase_count;
        metadata_words[6*16+:32]=first_phase;
        metadata_words[8*16+:32]=last_phase;
        metadata_words[10*16+:32]=frame_ftw;
        metadata_words[12*16+:32]=frame_tag;
        // 入口寄存与CIC输出寄存共两拍，时间戳退回窗口末端原始样点。
        metadata_words[14*16+:64]=first_tick-((captured_log2!=0)?64'd2:64'd0);
        metadata_words[18*16+:64]=last_tick-((captured_log2!=0)?64'd2:64'd0);
        metadata_words[22*16+:32]={12'b0,phase_interval};
        metadata_words[24*16+:16]={15'b0,phase_inverted};
        metadata_words[26*16+:16]=16'd2;
        metadata_words[27*16+:16]={11'b0,captured_log2};
        metadata_words[28*16+:16]={15'b0,(captured_log2!=0)};
        metadata_words[29*16+:16]=(captured_log2!=0)?16'd2:16'd0;
    end
    fdw_phase_query_bridge phase_query(
        .control_clk(control_clk),.sample_clk(sample_clk),.control_resetn(control_resetn),
        .sample_resetn(aresetn),.command_word(phase_command_word),.legacy_status(legacy_status),
        .metadata_words(metadata_words),.status_word(phase_status_word),
        .capture_busy(capture_active||capture_start_sync),.configure(configure),.config_log2(config_log2)
    );

endmodule
