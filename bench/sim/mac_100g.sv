`timescale 1ps/1fs
module tb;
localparam DW = @DW@, LANES = @LANES@, PACKETS = @PACKETS@;
reg rst = 1;
reg started = 0;
@CLOCKS@
@DECLARATIONS@
dut dut(@PORTS@);

reg [577:0] rx_words [0:@RX_WORDS@-1];
reg [577:0] tx_words [0:@TX_WORDS@-1];
reg [DW+LANES+1:0] app_words [0:@APP_WORDS@-1];
integer rx_schedule [0:@RX_WORDS@-1];
integer lengths [0:PACKETS-1];
reg [PACKETS-1:0] seen = 0;
time arrived [0:PACKETS-1];
integer rx_cycle = 0, rx_index = 0, rx_sent = 0;
integer sys_cycle = 0, tx_cycle = 0, tx_index = 0, tx_packets = 0;
integer rx_received = 0, rx_offset = 0, rx_id = 0;
integer rx_bytes = 0, rx_first_bytes = 0, tx_bits = 0;
integer tx_offset = 0, rx_previous_id = -1;
reg tx_done = 0;
time tx_deadline = 0, tx_first = 0, tx_last = 0, rx_first = 0, rx_last = 0;
time max_latency = 0;
reg tx_stalled = 0, rx_stalled = 0;
reg [577:0] tx_held;
reg [DW+2*LANES+96:0] rx_held;
wire [577:0] tx_item = {mac_tx_user, mac_tx_last, mac_tx_keep, mac_tx_data};
wire [DW+2*LANES+96:0] rx_item = {app_rx_ip_address, app_rx_length, app_rx_src_port,
    app_rx_dst_port, app_rx_last, app_rx_be, app_rx_error, app_rx_data};

function integer frame_bits(input integer length);
    frame_bits = (length + 42 + 4 + 8 + 12)*8;
endfunction

// The receive MAC never waits for ready. Frame start times account for FCS, preamble and IFG.
always @(negedge eth_rx_clk) if (started) begin
    mac_rx_valid = 0;
    if (!rst && rx_index < @RX_WORDS@ && rx_cycle == rx_schedule[rx_index]) begin
        {mac_rx_user, mac_rx_last, mac_rx_keep, mac_rx_data} = rx_words[rx_index];
        mac_rx_valid = 1;
        rx_index = rx_index + 1;
    end
    rx_cycle = rx_cycle + 1;
end
always @(posedge eth_rx_clk) if (!rst && started && mac_rx_valid && mac_rx_last) begin
    arrived[rx_sent] = $time;
    rx_sent = rx_sent + 1;
end

// A bounded application pause followed by periodic stalls exercises queue pressure.
always @(negedge sys_clk) if (started) begin
    app_rx_ready = !rst && sys_cycle >= @STALL_CYCLES@ &&
        (@STALL_CYCLES@ == 0 || (sys_cycle % 19 >= 3));
    sys_cycle = sys_cycle + 1;
end

// A 100G transmitter accepts a complete frame at each wire deadline, with optional ready stalls.
always @(negedge eth_tx_clk) begin
    mac_tx_ready = !rst && (tx_offset != 0 || $time >= tx_deadline) &&
        (@STALL_CYCLES@ == 0 || (tx_cycle % 17 >= 2));
    tx_cycle = tx_cycle + 1;
end
always @(posedge eth_tx_clk) if (!rst && started) begin
    if (tx_offset != 0 && !mac_tx_valid)
        $fatal(1, "TX underrun inside a committed frame");
    if (tx_stalled && (!mac_tx_valid || tx_item !== tx_held))
        $fatal(1, "TX changed while stalled");
    tx_stalled = mac_tx_valid && !mac_tx_ready;
    tx_held = tx_item;
    if (mac_tx_valid && mac_tx_ready) begin
        if (tx_index >= @TX_WORDS@ || {mac_tx_user, mac_tx_last, mac_tx_keep} !== tx_words[tx_index][577:512])
            $fatal(1, "Incorrect TX framing at word %d", tx_index);
        for (integer lane=0; lane<64; lane=lane+1)
            if (mac_tx_keep[lane] && mac_tx_data[8*lane +: 8] !== tx_words[tx_index][8*lane +: 8])
                $fatal(1, "Incorrect TX byte at word %d lane %d", tx_index, lane);
        if (tx_offset == 0) tx_deadline = $time + frame_bits(lengths[tx_packets])*10;
        tx_index = tx_index + 1;
        tx_offset = tx_offset + 64;
        if (mac_tx_last) begin
            tx_offset = 0;
            if (tx_packets == 0) tx_first = $time;
            else tx_bits = tx_bits + frame_bits(lengths[tx_packets]);
            tx_last = $time;
            tx_packets = tx_packets + 1;
        end
    end
end

// Payload and metadata are checked independently of the transmitted HDL headers.
always @(posedge sys_clk) if (!rst && started) begin
    if (rx_stalled && (!app_rx_valid || rx_item !== rx_held))
        $fatal(1, "RX changed while stalled");
    rx_stalled = app_rx_valid && !app_rx_ready;
    rx_held = rx_item;
    if (app_rx_valid && app_rx_ready) begin
        if (rx_offset == 0) begin
            rx_id = app_rx_data[31:0];
            if (rx_id < 0 || rx_id >= PACKETS || rx_id <= rx_previous_id || seen[rx_id])
                $fatal(1, "Unexpected, duplicate or reordered RX packet %d", rx_id);
            if (@ERROR_EVERY@ != 0 && (rx_id + 1) % @ERROR_EVERY@ == 0)
                $fatal(1, "An errored frame escaped the MAC queue");
        end
        if (app_rx_ip_address !== 32'hc0a80101 || app_rx_length !== lengths[rx_id] ||
            app_rx_src_port !== 1000 + rx_id || app_rx_dst_port !== 2000 || (app_rx_error & app_rx_be) != 0)
            $fatal(1, "Incorrect RX metadata");
        if (app_rx_be == 0 || (app_rx_be & (app_rx_be + 1)) != 0)
            $fatal(1, "Invalid RX byte mask");
        for (integer lane=0; lane<LANES; lane=lane+1) if (app_rx_be[lane]) begin
            if (rx_offset < 4) begin
                if (app_rx_data[8*lane +: 8] !== ((rx_id >> (8*rx_offset)) & 255))
                    $fatal(1, "Incorrect RX packet identifier");
            end else if (app_rx_data[8*lane +: 8] !== ((rx_offset + rx_id) & 255))
                $fatal(1, "Incorrect RX payload");
            rx_offset = rx_offset + 1;
        end
        if (app_rx_last) begin
            if (rx_offset != lengths[rx_id]) $fatal(1, "Truncated RX packet");
            if (rx_received == 0) begin rx_first = $time; rx_first_bytes = rx_offset; end
            rx_last = $time;
            rx_bytes = rx_bytes + rx_offset;
            if ($time - arrived[rx_id] > max_latency) max_latency = $time - arrived[rx_id];
            seen[rx_id] = 1;
            rx_previous_id = rx_id;
            rx_offset = 0;
            rx_received = rx_received + 1;
        end
    end
end

task send_word(input integer index, input integer packet);
begin
    @(negedge sys_clk);
    app_tx_data = app_words[index][DW-1:0];
    app_tx_be = app_words[index][DW +: LANES];
    app_tx_last = app_words[index][DW+LANES];
    app_tx_length = lengths[packet];
    app_tx_ip_address = 32'hc0a801ff;
    app_tx_src_port = 1000 + packet;
    app_tx_dst_port = 2000;
    app_tx_valid = 1;
    @(posedge sys_clk);
    while (!app_tx_ready) @(posedge sys_clk);
end
endtask

initial begin : stimulus
    integer packet;
    $readmemh("rx_words.hex", rx_words);
    $readmemh("tx_words.hex", tx_words);
    $readmemh("app_words.hex", app_words);
    $readmemh("rx_schedule.hex", rx_schedule);
    $readmemh("lengths.hex", lengths);
    repeat (8) @(negedge sys_clk);
    rst = 0;
    if (@RESET_TEST@) begin
        // Interrupt incomplete frames in both directions before the measured run.
        @(negedge sys_clk);
        app_tx_valid = 1; app_tx_last = 0; app_tx_be = {LANES{1'b1}};
        app_tx_length = 1472; app_tx_ip_address = 32'hc0a801ff;
        app_tx_src_port = 1000; app_tx_dst_port = 2000;
        @(posedge sys_clk);
        while (!app_tx_ready) @(posedge sys_clk);
        @(negedge sys_clk); app_tx_valid = 0;
        @(negedge eth_rx_clk);
        mac_rx_valid = 1; mac_rx_last = 0; mac_rx_keep = {64{1'b1}};
        @(negedge eth_rx_clk); mac_rx_valid = 0;
        repeat (8) @(negedge sys_clk);
        rst = 1;
        repeat (8) @(negedge sys_clk);
        rst = 0;
    end
    @(negedge sys_clk); started = 1;
    packet = 0;
    for (integer i=0; i<@APP_WORDS@; i=i+1) begin
        send_word(i, packet);
        if (app_words[i][DW+LANES]) packet = packet + 1;
    end
    @(negedge sys_clk); app_tx_valid = 0; tx_done = 1;
end

initial begin
    wait (tx_done && tx_packets == PACKETS && rx_sent == PACKETS);
    repeat (@DRAIN_CYCLES@) @(posedge sys_clk);
    if (rx_offset != 0 || rx_received + rx_drops != PACKETS || rx_packets != PACKETS)
        $fatal(1, "RX accounting mismatch: received %d dropped %d sent %d", rx_received, rx_drops, rx_packets);
    $display("{\"packets\":%0d,\"rx_received\":%0d,\"rx_dropped\":%0d,\"rx_bad\":%0d,\"rx_loss_fraction\":%.6f,\"tx_mpps\":%.6f,\"rx_mpps\":%.6f,\"tx_wire_gbps\":%.6f,\"rx_payload_gbps\":%.6f,\"max_rx_latency_ns\":%.3f,\"sys_clk_freq\":@SYS_FREQ@,\"offered_rate\":@RATE@}",
        PACKETS, rx_received, rx_drops, rx_bad_frames, rx_drops*1.0/PACKETS,
        (PACKETS-1)*1e6/(tx_last-tx_first),
        rx_received > 1 ? (rx_received-1)*1e6/(rx_last-rx_first) : 0.0,
        tx_bits*1000.0/(tx_last-tx_first),
        rx_received > 1 ? (rx_bytes-rx_first_bytes)*8000.0/(rx_last-rx_first) : 0.0, max_latency/1000.0);
    $finish;
end
initial begin
    repeat (@DRAIN_CYCLES@*20 + @STALL_CYCLES@ + 10000) @(posedge sys_clk);
    $fatal(1, "Simulation timed out");
end
endmodule
