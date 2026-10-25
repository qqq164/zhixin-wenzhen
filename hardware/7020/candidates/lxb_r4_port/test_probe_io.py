"""在独立目录运行两路 PWM/双向 IO 的真实原语仿真。"""
from pathlib import Path
import os
import json
import hashlib
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = ROOT / "outputs/checkpoint_110_hellofpga_migration_audit/probe_rtl_test_01"
VIVADO = Path("D:/Xilinx/2025.1/Vivado")


def main():
    """核对默认高阻、单路切换、双路不同周期以及复位释放。"""
    OUT.mkdir(exist_ok=False)
    files = [HERE / "rtl/fdw_pwm_core.v", HERE / "rtl/fdw_lxb_probe_io.v",
             HERE / "tb_lxb_probe_io.sv", VIVADO / "data/verilog/src/glbl.v"]
    env = dict(os.environ, PROCESSOR_ARCHITECTURE="AMD64")
    commands = [
        ["xvlog.bat", "--sv", "--work", "xil_defaultlib", *map(str, files)],
        ["xelab.bat", "--relax", "--debug", "typical", "-L", "unisims_ver",
         "xil_defaultlib.tb_lxb_probe_io", "xil_defaultlib.glbl", "-s", "lxb_probe"],
        ["xsim.bat", "lxb_probe", "-tclbatch", "run.tcl", "-log", "simulate.log"],
    ]
    (OUT / "run.tcl").write_text("run all\nexit\n", encoding="ascii")
    passed = False
    try:
        for index, command in enumerate(commands):
            result = subprocess.run([str(VIVADO / "bin" / command[0]), *command[1:]], env=env,
                                    cwd=OUT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (OUT / f"console_{index}.log").write_bytes(result.stdout)
            if result.returncode:
                print(result.stdout.decode("utf-8", errors="replace")[-3000:])
                raise RuntimeError("探针仿真工具执行失败")
        text = (OUT / "simulate.log").read_text(encoding="utf-8", errors="replace")
        passed = "FDW_LXB_PROBE_IO_PASS" in text and "Fatal:" not in text
        print(text[-1500:])
        if not passed:
            raise RuntimeError("两路探针功能仿真未通过")
    finally:
        (OUT / "verification.json").write_text(json.dumps({"passed": passed, "board_measured": False,
            "sources": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
