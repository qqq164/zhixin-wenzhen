#ifndef FDW_ADC_TEST_PLATFORM_H
#define FDW_ADC_TEST_PLATFORM_H
#include "mock_platform.h"
/* 只供ADC驱动行为模型使用，不替代真实BSP或DMA硬件。 */
typedef uintptr_t UINTPTR;
typedef uint32_t u32;
typedef struct { UINTPTR RegBase; } XAxiDma;
typedef struct { UINTPTR BaseAddr; } XAxiDma_Config;
typedef struct { UINTPTR BaseAddr; } XGpio;
#define XST_SUCCESS 0
#define XPAR_ADC_DMA_BASEADDR 0x40400000U
#define XPAR_CAPTURE_GPIO_BASEADDR 0x41200000U
#define XAXIDMA_IRQ_ALL_MASK 0x7000U
#define XAXIDMA_DEVICE_TO_DMA 1
#define XAXIDMA_RX_OFFSET 0x30U
#define XAXIDMA_SR_OFFSET 0x04U
#define XAXIDMA_BUFFLEN_OFFSET 0x28U
#define XAXIDMA_ERR_ALL_MASK 0x0770U
XAxiDma_Config *XAxiDma_LookupConfig(UINTPTR address);
int XAxiDma_CfgInitialize(XAxiDma *dma, XAxiDma_Config *config);
int XAxiDma_HasSg(XAxiDma *dma);
void XAxiDma_Reset(XAxiDma *dma);
int XAxiDma_ResetIsDone(XAxiDma *dma);
void XAxiDma_IntrDisable(XAxiDma *dma, u32 mask, int direction);
int XAxiDma_SimpleTransfer(XAxiDma *dma, UINTPTR address, u32 bytes, int direction);
int XAxiDma_Busy(XAxiDma *dma, int direction);
u32 XAxiDma_ReadReg(UINTPTR base, u32 offset);
int XGpio_Initialize(XGpio *gpio, UINTPTR address);
void XGpio_SetDataDirection(XGpio *gpio, unsigned channel, u32 direction);
void XGpio_DiscreteWrite(XGpio *gpio, unsigned channel, u32 data);
u32 XGpio_DiscreteRead(XGpio *gpio, unsigned channel);
void Xil_DCacheFlushRange(UINTPTR address, u32 bytes);
void Xil_DCacheInvalidateRange(UINTPTR address, u32 bytes);
int usleep(unsigned long usec);
#endif
