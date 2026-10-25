"""两板 PWM 通信自检，不采集 ADC、不写旧版记录、不访问外部激励引脚。"""

import argparse
import json
import socket
import time
from pathlib import Path


def command(host, text, fragments=None):
    """每条连接发送一个命令，读至换行，限制回复长度。"""
    with socket.create_connection((host, 5001), timeout=3) as connection:
        connection.settimeout(3)
        for block in fragments or [(text + "\n").encode("ascii")]:
            connection.sendall(block)
        received = bytearray()
        while b"\n" not in received:
            block = connection.recv(512)
            if not block:
                raise ConnectionError("回复在换行前结束")
            received.extend(block)
            if len(received) > 4096:
                raise ValueError("回复超过长度限制")
        return json.loads(received.split(b"\n", 1)[0])


def verify(host):
    """验证真实网口和 PL 寄存器状态，最后保持 PWM 关闭。"""
    report = {"host": host, "passed": False, "adc_sampled": False,
              "wujing_pl_executed": False, "npu_executed": False, "records": []}
    payload = b"FDW_PWM_TWO_BOARD_ECHO\n"
    with socket.create_connection((host, 7), timeout=3) as connection:
        connection.settimeout(3)
        connection.sendall(payload)
        reply = bytearray()
        while len(reply) < len(payload):
            block = connection.recv(len(payload) - len(reply))
            if not block:
                raise ConnectionError("回显提前关闭")
            reply.extend(block)
    assert bytes(reply) == payload, "回显数据不一致"
    report["echo_passed"] = True

    def run(text, fragments=None):
        response = command(host, text, fragments)
        report["records"].append({"command": text, "response": response})
        return response

    info = run("INFO")
    assert info["ok"] and info["project"] == "FDW_PWM", "当前不是新 PWM 服务"
    assert not info["external_pwm_pin"], "当前自检只允许内部 PWM"
    assert run("PING", [b"PI", b"NG\r\n"])["pong"], "拆包命令失败"
    try:
        assert run("PWM_ENABLE 0")["ok"]
        assert run("PWM_SET 1000000 500000")["accepted"]
        time.sleep(0.02)
        status = run("PWM_STATUS")
        assert status["period_ticks"] == 1_000_000 and status["high_ticks"] == 500_000
        assert not status["enabled"]
        assert run("PWM_ENABLE 1")["ok"]
        first = run("PWM_STATUS")
        time.sleep(0.05)
        second = run("PWM_STATUS")
        assert second["enabled"] and second["flags"] & 1
        assert ((second["cycle_count"] - first["cycle_count"]) & 0xFFFFFFFF) > 0, "PL 周期事件数没有增长"
        assert not run("PWM_SET 0 1")["ok"]
        assert not run("PWM_ENABLE 2")["ok"]
        assert run("PWM_SET 1000 400")["accepted"]
        time.sleep(0.02)
        status = run("PWM_STATUS")
        assert status["period_ticks"] == 1000 and status["high_ticks"] == 400
        assert run("GET_ADC")["error"] == "ADC_NOT_READY"
        report["passed"] = True
    finally:
        report["final_disable"] = run("PWM_ENABLE 0")
        report["final_status"] = run("PWM_STATUS")
        assert report["final_disable"]["ok"] and not report["final_status"]["enabled"]
    return report


def main():
    """保存到调用者明确指定的新目录；兼容悟净 Python 3.8。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.126.50")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.host)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("FDW_PWM_VERIFY=" + json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
