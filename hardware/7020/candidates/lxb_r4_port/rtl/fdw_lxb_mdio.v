`timescale 1ns / 1ps

// 官方 GMII/RGMII 桥的 MDIO 三态信号连接至实际单线 PHY 接口。
module fdw_lxb_mdio (
    input wire mdio_o,
    input wire mdio_t,
    output wire mdio_i,
    inout wire mdio
);
    IOBUF mdio_pad (.I(mdio_o), .O(mdio_i), .T(mdio_t), .IO(mdio));
endmodule
