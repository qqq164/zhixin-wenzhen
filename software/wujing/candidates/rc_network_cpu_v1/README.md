# 悟净CPU RC节点求解器V1

2026-10-23部署更新：board_final快照已在悟净/root/fdw_next_validation/rc_cpu_v1_20261023_board_final部署，25项板上测试和跨CPU结果对照通过。源码在SD根分区，按需运行；本地最新验收见outputs/checkpoint_112_cpu_rc_solver/board_final_acceptance.json及docs/CPU_RC离线求解器部署验收112.md。旧离线部署包保持原摘要。

2026-10-23。独立候选，正式PL、既有运行程序及SD启动镜像保持原样。Goertzel按用户安排延期。本模块只处理每个频点的少量复相量，不在CPU重新解调8192点原码。

## 已实现范围

- 已知拓扑、已知正值R/C参数的复数节点方程预测；支持桥接、多节点和多个固定节点电压边界。
- 1至64个非地节点、最多512个元件、一次1至256个正频率；默认示例扫1/10/100/1k/10k/100kHz。
- 复数LU部分主元选择、行均衡、条件数和归一化后向误差检查；悬空子网、病态方程及无效输入拒绝求解。
- 理论传递函数与输入测量传递函数的复数偏差比较；来源、模型、节点、测量组和相位参考身份检查。
- 当前R4历史PL结果的独立适配：按调用方提供的节点映射、同一DDS配置及帧元数据归一化。

## 首版边界

本版没有根据未知R/C反演元件值，没有故障元件定位或训练模型，也没有自动运动、节点选通、实时采集或网页绑定。队友的实际RC原理图和标称值尚未提供，examples中的两级RC只是验证电路。

首版采用被动R/C和固定节点电压边界，不支持电感、非线性器件、浮置电压源、受控源或理想零欧电阻。短接节点应在输入模型中合并，开路元件应移除，再检查拓扑可解性。不支持DC；正弦稳态不能代表未稳定的瞬态。

## 输入模型和单位

参见examples/model_rc_ladder.json。元件R的value单位Ω，C单位F；所有频率单位Hz。ground不列入nodes。reference_node必须是固定边界，boundary_voltages_relative中该节点的归一化复电压为[1,0]；地电压自动为零。

相量约定为exp(+jωt)，电容导纳为+jωC。transfer_real_imag是相对于参考节点的无量纲比值，不是伏特；支路电流单位是每参考伏的安培，即西门子，不能直接当作实际安培。只有输入测量已做幅相校准，才可以用真实参考电压换算绝对电压/电流。

模型可显式加入实际Rs、驱动串联电阻、探头输入电阻/电容；边界应放在真正已知或测到的节点。如果用Rs前A的实测电压作归一化边界，A上游的输出阻抗不应重复加入A之后。

reference_v_over_boundary_current_ohm表示参考电压除以该边界提供的总电流；有多个同时驱动的固定边界时，不应直接把它叫作单源无源输入阻抗。

## 运行方法

在本目录或将本目录完整复制到悟净新目录后运行，运行时只需要Python标准库：

```powershell
python -B run_rc_network.py --model examples/model_rc_ladder.json --output prediction_new.json
python -B test_rc_network.py
```

输出文件必须不存在。加入--measurements输入文件后执行比较；数据协议为FDW_RC_MEASUREMENTS_V1，使用实部/虚部二元数组，而非未注明单位的角度。解析生成的输入例子在outputs/checkpoint_112_cpu_rc_solver/verification_final/analytic_measurements.json。

比较门限是absolute_tolerance + relative_tolerance × |理论传递函数|。默认0.01和0.05只是软件入口的工程占位值，不是由本板噪声、R/C容差或真实故障样本推导的验收标准。结果只表示是否落在该门限内，不自动宣布元件正常或故障。幅度接近数值零时不报告相位误差；实际噪声下是否能使用该相位，还需要上游信号质量门限。

## 接现有PL结果

normalize_pl_group接受“节点名→帧对象”的显式映射。每帧包含metadata和actual；校验R4元数据版本、8192点、时钟/DDS频率、配置tag、首末tick/相位、帧号、ABI、饱和状态及PL相位归一完成标志。不同节点的相量须已归一到同一DDS参考，不把不同启动时刻的原始相角直接相减。

从actual.dds_relative_real_imag_counts取未校准复相量；仅当每条支路同时提供幅度/相位校准标志、calibration_id、calibration_evidence_sha256及calibrated_dds_relative_real_imag_counts时，才整组切换到校准后的数值。缺任何一条支路的校准，则整组都用原始尺度，只作验证，不混用校准前后相量。校准身份及证据真实性仍由上游校准流程保证。

现有R4结果没有上述完整模拟幅相校准，因此只能验证适配和计算，不能给出正式DUT物理判断。跨帧接触状态、同一激励会话、Va→Vb→Va漂移复测及测点路由由后续调度器保证；本模块没有替代这些硬件与流程验收。

历史验证使用99保存的同一ADC节点连续两帧，映射名称REF_REPEAT/SAME_NODE_REPEAT仅用于验证重复相量归一化。它不是A/B物理测点，不是新增采样，也不是RC负载实测。

## 部署与回退

tools/deploy_rc_network_cpu112.py先准备独立上传包，再以--run-staged运行板上CPU检查。远端目录在/root/fdw_next_validation/rc_cpu_v1_20261023_*，目录和输出存在即拒绝覆盖。该入口不打开SDK设备、不访问PL寄存器、不连接7020、不启DAC、不停网页、不装依赖、不写启动分区，也不注册开机任务。

验证将记录悟净CPU架构/Python、启动身份、网页状态、25项边界测试和三份计算结果，并回收与电脑对照。没有取得板端执行证据前，不能称已部署或已上板通过。

## 算法依据

- [Qucs交流节点分析](https://qucs.sourceforge.net/tech/node18.html)：交流分析使用复数节点方程。
- [LAPACK误差界说明](https://www.netlib.org/lapack/lug/node81.html)：后向误差与条件数须结合解释。本实现是小矩阵标准Python实现，没有调用板上LAPACK；NumPy/LAPACK仅在电脑独立对照中使用。
