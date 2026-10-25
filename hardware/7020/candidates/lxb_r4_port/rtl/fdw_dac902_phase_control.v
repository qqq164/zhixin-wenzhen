`timescale 1ns / 1ps

// DIG5的DAC902控制桥：暂存参数、原子提交、目的域确认和独立管脚许可。
// GPIO命令：bit31请求翻转，bit30管脚许可，bit29:27操作码，bit26:0参数。
module fdw_dac902_phase_control #(
    parameter ROM_FILE = "fdw_sine12_4096.mem"
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 control_clk CLK" *)
    input wire control_clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 control_resetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME control_resetn, POLARITY ACTIVE_LOW" *)
    input wire control_resetn,
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 sample_clk CLK" *)
    input wire sample_clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 sample_resetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME sample_resetn, POLARITY ACTIVE_LOW" *)
    input wire sample_resetn,
    input wire [31:0] command_word,
    output wire [31:0] status_word,
    output wire [11:0] dac_data,
    output wire dac_clk,
    output wire reference_valid,
    output wire reference_inverted,
    output wire [31:0] reference_phase,
    output wire [31:0] reference_ftw,
    output wire [31:0] reference_tag
);
    wire arstn = control_resetn && sample_resetn;
    reg [1:0] state;
    reg acknowledged, pending_request, permit_seen_low, interface_initialized;
    reg [3:0] error_code;
    reg [3:0] query;
    reg [31:0] staged_ftw, staged_phase, tag_counter;
    reg [11:0] staged_gain, staged_offset;
    reg staged_enable, staged_invert, staged_restart;
    reg [122:0] config_packet, applied_config;
    reg [31:0] applied_tag;
    wire config_ready, config_valid, config_consumed;
    wire [122:0] config_data;
    wire response_valid;
    wire [33:0] response_data;
    wire [2:0] opcode = command_word[29:27];
    wire [26:0] argument = command_word[26:0];
    wire busy = state != 0;
    wire [31:0] next_tag = tag_counter + 1'b1;

    fdw_cdc_mailbox #(.WIDTH(123)) configuration_mailbox (
        .src_clk(control_clk), .dst_clk(sample_clk), .arstn(arstn),
        .src_valid(state == 1), .src_ready(config_ready), .src_data(config_packet),
        .dst_valid(config_valid), .dst_ready(config_consumed), .dst_data(config_data)
    );

    reg reply_pending, sample_pending, awaiting_sample;
    reg [31:0] sample_tag_pending;
    reg [33:0] reply_data;
    wire reply_ready;
    assign config_consumed = !sample_pending && !reply_pending;
    fdw_cdc_mailbox #(.WIDTH(34)) confirmation_mailbox (
        .src_clk(sample_clk), .dst_clk(control_clk), .arstn(arstn),
        .src_valid(reply_pending), .src_ready(reply_ready), .src_data(reply_data),
        .dst_valid(response_valid), .dst_ready(state == 2), .dst_data(response_data)
    );

    wire [11:0] generated_data;
    wire generated_clock, cfg_accepted, cfg_rejected;
    wire [31:0] sample_phase, sample_tag;
    wire sample_active, sample_clipped, sample_valid;
    fdw_dac902_dds #(.ROM_FILE(ROM_FILE)) waveform (
        .sample_clk(sample_clk), .aresetn(arstn),
        .cfg_valid(config_valid && config_consumed),
        .cfg_frequency_word(config_data[31:0]), .cfg_phase_offset(config_data[63:32]),
        .cfg_gain_q11(config_data[75:64]), .cfg_offset_code(config_data[87:76]),
        .cfg_enable(config_data[88]), .cfg_invert(config_data[89]), .cfg_restart(config_data[90]),
        .cfg_tag(config_data[122:91]), .cfg_accepted(cfg_accepted), .cfg_rejected(cfg_rejected),
        .sample_code(generated_data), .sample_phase(sample_phase),
        .sample_tag(sample_tag), .sample_active(sample_active),
        .sample_clipped(sample_clipped), .sample_valid(sample_valid)
    );

    // 许可只同步单比特；撤销许可直接关闭三态，即使采样钟停住也能断开数字驱动。
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] permit_sync;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] connected_sync;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] clipped_sync;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] parked_sync;
    reg sample_initialized, pad_release, clock_run;
    wire permit_authorized = command_word[30] && permit_seen_low;
    wire permit_resetn = arstn && permit_authorized;
    // 每个管脚的三态控制独立放入IOB，消除控制钟经长组合逻辑直达输出的路径。
    // 同步器异步撤销、同步释放；即使采样钟停止也能异步置三态。
    (* IOB = "TRUE", KEEP = "TRUE", DONT_TOUCH = "TRUE" *) reg [12:0] pad_tristate;
    wire pins_enabled = !pad_tristate[12];
    always @(posedge sample_clk or negedge permit_resetn) begin
        if (!permit_resetn) permit_sync <= 0;
        else permit_sync <= {permit_sync[0], 1'b1};
    end
    always @(posedge sample_clk or negedge permit_sync[1]) begin
        if (!permit_sync[1]) pad_tristate <= 13'h1fff;
        else pad_tristate <= {13{!pad_release}};
    end
    // 接通时先在低电平释放IO三态，再启动ODDR；避免首拍时钟成为窄脉冲。
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("ASYNC")) dac_clk_oddr (
        .Q(generated_clock), .C(sample_clk), .CE(1'b1),
        .D1(1'b0), .D2(1'b1), .R(!arstn || !clock_run), .S(1'b0)
    );
    genvar bit_index;
    generate for (bit_index=0; bit_index<12; bit_index=bit_index+1) begin: output_buffers
        OBUFT data_pad (.I(generated_data[bit_index]), .T(pad_tristate[bit_index]), .O(dac_data[bit_index]));
    end endgenerate
    OBUFT clock_pad (.I(generated_clock), .T(pad_tristate[12]), .O(dac_clk));

    always @(posedge sample_clk or negedge arstn) begin
        if (!arstn) begin
            sample_initialized <= 0;
            pad_release <= 0;
            clock_run <= 0;
            sample_pending <= 0;
            awaiting_sample <= 0;
            sample_tag_pending <= 0;
            reply_pending <= 0;
            reply_data <= 0;
        end else begin
            pad_release <= permit_sync[1] && sample_initialized;
            clock_run <= pins_enabled && permit_sync[1];
            if (sample_valid) sample_initialized <= 1;
            if (reply_pending && reply_ready) reply_pending <= 0;
            if (config_valid && config_consumed) begin
                sample_pending <= 1;
                awaiting_sample <= 0;
                sample_tag_pending <= config_data[122:91];
            end else if (sample_pending) begin
                if (cfg_rejected) begin
                    reply_data <= {sample_tag_pending, 1'b1, sample_active};
                    reply_pending <= 1;
                    sample_pending <= 0;
                end else if (cfg_accepted) begin
                    awaiting_sample <= 1;
                end else if (awaiting_sample && sample_valid && sample_tag == sample_tag_pending) begin
                    reply_data <= {sample_tag_pending, 1'b0, sample_active};
                    reply_pending <= 1;
                    sample_pending <= 0;
                    awaiting_sample <= 0;
                end
            end
        end
    end

    always @(posedge control_clk or negedge arstn) begin
        if (!arstn) begin
            permit_seen_low <= 0;
            connected_sync <= 0;
            clipped_sync <= 0;
            parked_sync <= 0;
        end else begin
            // 复位后必须先看见许可撤销，不能沿用复位前残留的高电平自动接通。
            if (!command_word[30]) permit_seen_low <= 1;
            connected_sync <= {connected_sync[0], pins_enabled};
            clipped_sync <= {clipped_sync[0], sample_clipped};
            parked_sync <= {parked_sync[0], !pad_release && !clock_run};
        end
    end

    reg [15:0] queried_value;
    always @* begin
        case (query)
            0: queried_value = 16'hD905;
            1: queried_value = applied_tag[15:0];
            2: queried_value = applied_tag[31:16];
            3: queried_value = applied_config[15:0];
            4: queried_value = applied_config[31:16];
            5: queried_value = applied_config[47:32];
            6: queried_value = applied_config[63:48];
            7: queried_value = {4'b0, applied_config[75:64]};
            8: queried_value = {4'b0, applied_config[87:76]};
            9: queried_value = {13'b0, applied_config[90:88]};
            default: queried_value = 0;
        endcase
    end
    assign status_word = {acknowledged, busy, (config_ready && interface_initialized), connected_sync[1],
                          (error_code != 0), applied_config[88], clipped_sync[1],
                          arstn, error_code, parked_sync[1], 3'b0, queried_value};

    always @(posedge control_clk or negedge arstn) begin
        if (!arstn) begin
            state <= 0;
            interface_initialized <= 0;
            acknowledged <= 0;
            pending_request <= 0;
            error_code <= 0;
            query <= 0;
            staged_ftw <= 0;
            staged_phase <= 0;
            staged_gain <= 0;
            staged_offset <= 12'd2048;
            staged_enable <= 0;
            staged_invert <= 0;
            staged_restart <= 1;
            tag_counter <= 0;
            applied_tag <= 0;
            config_packet <= 0;
            applied_config <= 0;
        end else if (!interface_initialized) begin
            // 丢弃复位前GPIO残留的翻转位，不自动重放旧配置请求。
            acknowledged <= command_word[31];
            pending_request <= command_word[31];
            interface_initialized <= 1;
        end else if (state == 1) begin
            if (config_ready) state <= 2;
        end else if (state == 2) begin
            if (response_valid) begin
                if (response_data[33:2] != config_packet[122:91]) error_code <= 4;
                else if (response_data[1] || response_data[0] != config_packet[88]) error_code <= 3;
                else begin
                    applied_config <= config_packet;
                    applied_tag <= response_data[33:2];
                    error_code <= 0;
                end
                acknowledged <= pending_request;
                state <= 0;
            end
        end else if (command_word[31] != acknowledged) begin
            error_code <= 0;
            acknowledged <= command_word[31];
            case (opcode)
                0: begin
                    if (argument <= 9) query <= argument[3:0];
                    else error_code <= 1;
                end
                1: staged_ftw[26:0] <= argument;
                2: begin
                    if (argument <= 31) staged_ftw[31:27] <= argument[4:0];
                    else error_code <= 1;
                end
                3: staged_phase[26:0] <= argument;
                4: begin
                    if (argument <= 31) staged_phase[31:27] <= argument[4:0];
                    else error_code <= 1;
                end
                5: begin
                    staged_gain <= argument[11:0];
                    staged_offset <= argument[23:12];
                    staged_enable <= argument[24];
                    staged_invert <= argument[25];
                    staged_restart <= argument[26];
                end
                6: begin
                    if (argument != 0 || staged_ftw[31] || staged_gain > 2048) error_code <= 2;
                    else if (!config_ready) error_code <= 5;
                    else begin
                        config_packet <= {next_tag, staged_restart, staged_invert, staged_enable,
                                          staged_offset, staged_gain, staged_phase, staged_ftw};
                        tag_counter <= next_tag;
                        pending_request <= command_word[31];
                        acknowledged <= acknowledged;
                        state <= 1;
                    end
                end
                7: begin
                    if (command_word[30]) error_code <= 6;
                    else begin
                        staged_ftw <= 0;
                        staged_phase <= 0;
                        staged_gain <= 0;
                        staged_offset <= 12'd2048;
                        staged_enable <= 0;
                        staged_invert <= 0;
                        staged_restart <= 1;
                    end
                end
            endcase
        end
    end
    // 仅增加观测输出，DDS、输出三态和配置握手逐字保留R2实现。
    reg [31:0] reference_config_ftw,reference_config_tag;
    reg reference_config_inverted;
    always @(posedge sample_clk or negedge arstn)begin
        if(!arstn)begin reference_config_ftw<=0;reference_config_tag<=0;reference_config_inverted<=0;end
        else if(config_valid&&config_consumed&&!config_data[31]&&config_data[75:64]<=2048)begin
            reference_config_ftw<=config_data[31:0];
            reference_config_tag<=config_data[122:91];
            reference_config_inverted<=config_data[89];
        end
    end
    assign reference_phase=sample_phase;
    assign reference_tag=sample_tag;
    assign reference_ftw=reference_config_ftw;
    assign reference_inverted=reference_config_inverted;
    assign reference_valid=sample_valid&&sample_active&&pins_enabled&&clock_run&&
                           !sample_clipped&&sample_tag==reference_config_tag;

endmodule
