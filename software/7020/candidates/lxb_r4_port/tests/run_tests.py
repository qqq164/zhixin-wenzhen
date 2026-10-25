"""验证新增板级控制互锁，并回归真实 TCP 分段与低频协议。"""
from pathlib import Path
import os
import json
import subprocess
import argparse
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
SRC = HERE.parent / "src"
OUT = ROOT / "outputs/checkpoint_110_hellofpga_migration_audit/native_test_01"
OLD_TEST = ROOT / "software/7020/candidates/dig5_r4_lowrate_bulk/tests/test_tcp_server.c"
STUBS = ROOT / "tests/fixtures/7020_stubs"


def main():
    """所有模型和执行记录保存在本次新目录，原冻结回归记录不改。"""
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', required=True)
    args = parser.parse_args()
    if not args.attempt.replace('_', '').isalnum():
        raise ValueError('测试目录标识不合法')
    OUT = OUT.parent / args.attempt
    OUT.mkdir(parents=True, exist_ok=False)
    stubs = OUT / "stubs"
    stubs.mkdir()
    (stubs / "xil_io.h").write_text("#include <stdint.h>\nuint32_t Xil_In32(uintptr_t);\nvoid Xil_Out32(uintptr_t,uint32_t);\n", encoding="ascii")
    (stubs / "sleep.h").write_text("void usleep(unsigned long);\n", encoding="ascii")
    # 允许公开仓库使用本机编译器，兼容原有Windows工具链。
    cc = Path(os.environ.get("CC") or shutil.which("gcc") or "D:/Xilinx/2025.1/Vitis/tps/win64/msys64/mingw64/bin/gcc.exe")
    env = dict(os.environ, PATH=str(cc.parent) + os.pathsep + os.environ["PATH"])
    report = {"passed": False, "board_measured": False, "tests": []}
    try:
        # 模拟每一次 MDIO 读写失败，确认不会静默接受未配置的 PHY 延时。
        (stubs / "xemacps.h").write_text('#include <stdint.h>\ntypedef uint32_t u32;\ntypedef uint16_t u16;\ntypedef struct {int unused;} XEmacPs;\n#define XST_SUCCESS 0\nint XEmacPs_PhyRead(XEmacPs*,u32,u32,u16*);\nint XEmacPs_PhyWrite(XEmacPs*,u32,u32,u16);\n', encoding='ascii')
        command = [str(cc), "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", "-I"+str(stubs),
                   "-I"+str(SRC), str(SRC / "fdw_lxb_phy.c"), str(HERE / "test_lxb_phy.c"), "-o", str(OUT / "phy.exe")]
        result = subprocess.run(command, env=env, cwd=OUT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (OUT / "phy_compile.log").write_bytes(result.stdout)
        if result.returncode:
            raise RuntimeError(result.stdout.decode('utf-8', errors='replace'))
        result = subprocess.run([str(OUT / "phy.exe")], env=env, cwd=OUT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (OUT / "phy_run.log").write_bytes(result.stdout)
        if result.returncode or b'FDW_LXB_PHY_MODEL_PASS' not in result.stdout:
            raise RuntimeError('PHY延时模型验证失败')
        report['tests'].append({'name': 'phy_delays_and_fault_recovery', 'passed': True})
        command = [str(cc), "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", "-I"+str(stubs),
                   "-I"+str(SRC), str(SRC / "fdw_lxb_io.c"), str(HERE / "test_lxb_io.c"), "-o", str(OUT / "lxb_io.exe")]
        result = subprocess.run(command, env=env, cwd=OUT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (OUT / "io_compile.log").write_bytes(result.stdout)
        if result.returncode:
            raise RuntimeError(result.stdout.decode("utf-8", errors="replace"))
        result = subprocess.run([str(OUT / "lxb_io.exe")], env=env, cwd=OUT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (OUT / "io_run.log").write_bytes(result.stdout)
        if result.returncode or b"FDW_LXB_NATIVE_IO_PASS" not in result.stdout:
            raise RuntimeError("板级互锁模型失败")
        report["tests"].append({"name": "lxb_io_interlocks", "passed": True})

        # 沿用 R4 真实 TCP 模型，新增控制函数用可计数桩验证分派和忙时拒绝。
        text = OLD_TEST.read_text(encoding="utf-8")
        text = text.replace('../src/tcp_server.c', (SRC / 'tcp_server.c').as_posix())
        text = text.replace('../src/fdw_bulk_frame.c', (SRC / 'fdw_bulk_frame.c').as_posix())
        additions = r'''
static uint32_t lxb_relay, lxb_mask, lxb_calls;
void fdw_digital_invalidate(void) { }
int fdw_lxb_io_init(void) { return 0; }
void fdw_lxb_io_halt(void) { lxb_mask=0; }
void fdw_lxb_select_default_output(void) { if(!lxb_mask) lxb_mask=1; }
uint32_t fdw_lxb_output_mask(void) { return lxb_mask; }
uint32_t fdw_lxb_relay_mask(void) { return lxb_relay; }
int fdw_lxb_relay_set(uint32_t mask) { ++lxb_calls;if(mask>127)return -1;lxb_relay=mask;return 0; }
int fdw_lxb_pwm_set(uint32_t channel,uint32_t period,uint32_t high) { ++lxb_calls;return channel<2&&period&&high<=period?0:-1; }
int fdw_lxb_pwm_enable(uint32_t channel,uint32_t enabled) { ++lxb_calls;if(channel>1||enabled>1)return -1;return 0; }
int fdw_lxb_pwm_status(uint32_t channel,struct fdw_lxb_pwm_status *status) { if(channel>1)return -1;memset(status,0,sizeof(*status));return 0; }
'''
        text += additions
        marker = '    assert(start_application() == 0);'
        assert text.count(marker) == 1
        cases = r'''
    command_contains("LXB_STATUS\n", "\"pwm_channels\":2");
    command_contains("RELAY_SET 18\n", "\"ok\":true");
    command_contains("RELAY_SET 128\n", "\"ok\":false");
    command_contains("RELAY_SET 18 extra\n", "\"ok\":false");
    command_contains("IO_PWM_SET 0 1000 100\n", "\"ok\":true");
    command_contains("IO_PWM_SET 1 2000 800\n", "\"ok\":true");
    command_contains("IO_PWM_SET 2 1000 100\n", "\"ok\":false");
    command_contains("IO_PWM_ENABLE 1 1\n", "\"ok\":true");
    command_contains("IO_PWM_STATUS 1\n", "\"channel\":1");
    job_busy=1;
    command_contains("RELAY_SET 0\n", "\"error\":\"ADC_BUSY\"");
    command_contains("IO_PWM_ENABLE 0 1\n", "\"error\":\"ADC_BUSY\"");
    command_contains("LXB_STATUS\n", "\"ok\":true");
    job_busy=0;
'''
        text = text.replace(marker, cases + '\n' + marker)
        (OUT / "test_tcp.c").write_text(text, encoding="utf-8")
        command = [str(cc), "-std=c11", "-D__USE_MINGW_ANSI_STDIO=1", "-Wall", "-Wextra", "-Werror", "-O2",
                   "-I"+str(STUBS / "adc"), "-I"+str(STUBS), "-I"+str(SRC), str(OUT / "test_tcp.c"), "-o", str(OUT / "tcp.exe")]
        result = subprocess.run(command, env=env, cwd=OUT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (OUT / "tcp_compile.log").write_bytes(result.stdout)
        if result.returncode:
            raise RuntimeError(result.stdout.decode("utf-8", errors="replace"))
        result = subprocess.run([str(OUT / "tcp.exe")], env=env, cwd=OUT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (OUT / "tcp_run.log").write_bytes(result.stdout)
        if result.returncode or b"PASS" not in result.stdout:
            raise RuntimeError("迁移 TCP 回归失败："+result.stdout.decode("utf-8", errors="replace"))
        report["tests"].append({"name": "r4_tcp_and_lxb_commands", "passed": True})
        report["passed"] = True
        print("FDW_LXB_NATIVE_TESTS_PASS")
    finally:
        (OUT / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
